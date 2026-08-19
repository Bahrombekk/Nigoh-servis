"""Nigoh — qurilma skani: `202` + job, natijalar SSE bilan kanal sari.

Eski `/admin/scan` 64 kanalni tekshirib bitta javob qaytaradi —
foydalanuvchi oxirigacha (60 soniyagacha) kutadi. Bu yerda skan fonda
boradi: `POST /devices/scan` darhol `job_id` qaytaradi, birinchi topilgan
kanal bir-ikki soniyada SSE orqali keladi va UI ro'yxatni jonli
to'ldiradi. Job holati xotirada, muddati 10 daqiqa.
"""
import asyncio
import json
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse

from core import device_info as devinfo
from core import fast_start, security
from core.db import get_db
from core.rtsp_probe import build_rtsp_url, probe

from .config import CHANNEL_VENDORS, VENDORS
from .helpers import channel_path, resolve_ref
from .models import ScanIn

# Prefiks nisbiy — create_app uni /api/v1 (asosiy) va /api (eski) ostida ulaydi.
router = APIRouter(prefix="/devices", tags=["devices"])

JOB_TTL = 600.0        # soniya — 10 daqiqadan keyin job unutiladi
SCAN_WORKERS = 8       # parallel probe chegarasi (64 FFmpeg ochilmasin)
KEEPALIVE_S = 15.0

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def _purge_jobs() -> None:
    now = time.monotonic()
    with _jobs_lock:
        for key in [k for k, j in _jobs.items()
                    if now - j["created"] > JOB_TTL]:
            _jobs.pop(key, None)


def _get_job(job_id: str) -> dict:
    _purge_jobs()
    with _jobs_lock:
        job = _jobs.get(job_id)
    if job is None:
        raise HTTPException(404, "Skan topilmadi yoki muddati o'tgan")
    return job


def _emit(job: dict, event: str, data: dict) -> None:
    # Ro'yxatga faqat qo'shiladi — SSE o'quvchi indeks bilan yuradi,
    # qulf kerak emas (GIL ostida append atomar).
    job["events"].append((event, data))


def _probe_all(ip: str, port: int, jobs: dict, user: str, pw: str) -> dict:
    results: dict = {}
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
        futures = {pool.submit(probe, ip, port, path, user, pw): key
                   for key, path in jobs.items()}
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return results


def _run_scan(job: dict, job_id: str, max_channels: int) -> None:
    ip, port = job["ip"], job["port"]
    user, pw = job["username"], job["password"]

    def channel_event(channel: int, result: dict) -> dict:
        ok = bool(result.get("ok"))
        return {
            "channel": channel,
            "ok": ok,
            "codec": result.get("codec", ""),
            "resolution": result.get("resolution", ""),
            "needs_transcode": bool(result.get("needs_transcode")),
            "rtsp_path": channel_path(job["vendor"], channel, "main")
                         if job["vendor"] else "",
            "snapshot_url": (f"/api/v1/devices/scan/{job_id}/snapshot/{channel}"
                             if ok else ""),
        }

    try:
        # 1) Shablonni aniqlash — har ishlab chiqaruvchining 1-kanali.
        candidates = {v: channel_path(v, 1, "main") for v in CHANNEL_VENDORS}
        candidates["boshqa"] = "/stream1"
        first = _probe_all(ip, port, candidates, user, pw)

        if not any(r["ok"] for r in first.values()):
            # Eng ma'noli sababni tanlaymiz (parol > oqim > rtsp > tarmoq).
            message = "Qurilma javob bermadi"
            for stage in ("parol", "oqim", "rtsp", "tarmoq"):
                hit = next((r for r in first.values()
                            if r.get("stage") == stage), None)
                if hit:
                    message = hit["message"]
                    break
            _emit(job, "error", {"message": message})
            return

        vendor = next(v for v in [*CHANNEL_VENDORS, "boshqa"]
                      if first.get(v, {}).get("ok"))
        job["vendor"] = vendor
        vendor_name = next((v["name"] for v in VENDORS if v["id"] == vendor),
                           vendor)
        _emit(job, "meta", {"vendor": vendor, "vendor_name": vendor_name})
        _emit(job, "channel", channel_event(1, first[vendor]))
        live = 1

        # 2) Kanallarni sanash — 8 talik bloklarda, har natija kelgan
        # sari alohida hodisa. Bo'sh blok — qurilma tugadi.
        if vendor != "boshqa":
            start = 2
            while start <= max_channels:
                block = range(start, min(start + SCAN_WORKERS,
                                         max_channels + 1))
                paths = {c: channel_path(vendor, c, "main") for c in block}
                block_live = 0
                with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
                    futures = {pool.submit(probe, ip, port, p, user, pw): c
                               for c, p in paths.items()}
                    for future in as_completed(futures):
                        c = futures[future]
                        result = future.result()
                        _emit(job, "channel", channel_event(c, result))
                        if result.get("ok"):
                            block_live += 1
                live += block_live
                if not block_live:
                    break
                start += SCAN_WORKERS

        _emit(job, "done", {
            "found": True,
            "vendor": vendor,
            "vendor_name": vendor_name,
            "device": "nvr" if live > 1 else "camera",
            "live_channels": live,
        })
    except Exception as exc:                     # noqa: BLE001
        _emit(job, "error", {"message": f"Skan xatosi: {exc.__class__.__name__}"})
    finally:
        job["done"] = True


@router.get("/info")
def device_information(ip: str = "", username: str = "", password: str = "",
                       ref: str = ""):
    """Qurilma pasporti: manufacturer, model, firmware, serial, mac.

    Manba — ONVIF `GetDeviceInformation`, zaxira — Hikvision ISAPI.
    `ref` berilsa (ichki id yoki ext:...) saqlangan kamera ma'lumotlari
    ishlatiladi va topilgan model/firmware bazaga yozib qo'yiladi.
    """
    camera_id = None
    if ref:
        with get_db() as db:
            row = resolve_ref(db, ref)
        if row is None:
            raise HTTPException(404, "Kamera topilmadi")
        camera_id = row["id"]
        ip = ip or (row["ip"] or "")
        username = username or (row["username"] or "")
        password = password or security.decrypt(row["password_enc"])
    if not ip:
        raise HTTPException(400, "ip yoki ref bering")

    info = devinfo.device_info(ip, username, password)
    if info is None:
        raise HTTPException(502, "Qurilma pasport bermadi — ONVIF/ISAPI "
                                 "o'chiq yoki login noto'g'ri")
    if camera_id is not None and (info["model"] or info["firmware"]):
        with get_db() as db:
            db.execute("UPDATE cameras SET model = ?, firmware = ? "
                       "WHERE id = ?",
                       (info["model"], info["firmware"], camera_id))
    return info


@router.post("/scan", status_code=202)
def scan_start(body: ScanIn):
    """Skanni boshlaydi va darhol qaytadi — natijalar SSE'da.

    Javob: `job_id` va hodisalar manzili. Parol berilmasa, `camera_id`
    orqali saqlangan parol ishlatiladi (tahrirlash oynasi uchun).
    """
    _purge_jobs()
    pw = body.password
    if not pw and body.camera_id:
        with get_db() as db:
            row = db.execute("SELECT password_enc FROM cameras WHERE id = ?",
                             (body.camera_id,)).fetchone()
        if row:
            pw = security.decrypt(row["password_enc"])

    job_id = uuid.uuid4().hex[:12]
    job = {
        "created": time.monotonic(),
        "events": [],
        "done": False,
        "ip": body.ip.strip(),
        "port": body.port,
        "username": body.username.strip(),
        "password": pw or "",
        "vendor": "",
    }
    with _jobs_lock:
        _jobs[job_id] = job
    threading.Thread(target=_run_scan, args=(job, job_id, body.max_channels),
                     daemon=True).start()
    return {"job_id": job_id,
            "events": f"/api/v1/devices/scan/{job_id}/events"}


@router.get("/scan/{job_id}/events")
async def scan_events(job_id: str):
    """Skan natijalari SSE bilan: `meta`, har kanal uchun `channel`,
    oxirida `done` (yoki `error`). Kech ulangan mijoz ham hammasini
    boshidan oladi — hodisalar job xotirasida turadi."""
    job = _get_job(job_id)

    async def gen():
        index, last_send = 0, time.monotonic()
        while True:
            events = job["events"]
            while index < len(events):
                event, data = events[index]
                index += 1
                last_send = time.monotonic()
                yield (f"event: {event}\n"
                       f"data: {json.dumps(data, ensure_ascii=False)}\n\n")
                if event in ("done", "error"):
                    return
            if job["done"] and index >= len(job["events"]):
                return
            if time.monotonic() - last_send > KEEPALIVE_S:
                last_send = time.monotonic()
                yield ": keepalive\n\n"
            await asyncio.sleep(0.3)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


@router.get("/scan/{job_id}/snapshot/{channel}")
def scan_snapshot(job_id: str, channel: int):
    """Skanda topilgan kanalning JPEG surati — hali saqlanmagan qurilmadan.

    Avval kameraning HTTP-snapshot manzili, ishlamasa RTSP'dan bir kadr.
    """
    job = _get_job(job_id)
    vendor = job["vendor"] or "boshqa"
    path = channel_path(vendor, channel, "main")
    rtsp_url = build_rtsp_url(job["ip"], job["port"], path,
                              job["username"], job["password"])
    data = fast_start.device_snapshot(job["ip"], job["username"],
                                      job["password"], channel,
                                      vendor=vendor, rtsp_url=rtsp_url)
    if not data:
        raise HTTPException(404, "Surat olinmadi")
    return Response(data, media_type="image/jpeg",
                    headers={"Cache-Control": "max-age=10"})

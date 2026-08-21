"""Nigoh — boshqaruv endpointlari: kameralar CRUD, NVR import, skaner,
foydalanuvchilar va MediaMTX holati. Tugunlar alohida: api/nodes.py."""
import math
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Request

from core import device_info as devinfo
from core import fast_start, health, security
from core.db import get_db, unique_slug
from core.fast_start import channel_from_path
from core.rtsp_probe import probe
from media import reconciler
from media import sync as mediamtx_sync

from .config import CHANNEL_VENDORS, VENDORS
from .helpers import (
    admin_camera,
    cameras_for_mediamtx,
    channel_path,
    detect_codec,
    detect_sub_path,
    mask_config,
    require_admin,
    resolve_ref,
)
from .models import CameraIn, EnabledIn, NvrIn, ProbeIn, ScanIn, UserIn

# Prefiks nisbiy — create_app uni /api/v1 (asosiy) va /api (eski) ostida ulaydi.
router = APIRouter(prefix="/admin", tags=["admin"],
                   dependencies=[Depends(require_admin)])


def _fill_passport(camera_ids: list[int], ip: str,
                   username: str, password: str) -> None:
    """Qurilma pasportini (model/firmware) fonda so'rab bazaga yozadi.

    ONVIF/ISAPI sekin javob berishi mumkin — yaratish so'rovini
    kuttirmaslik uchun alohida ipda yuradi. Qurilma pasport bermasa
    jimgina o'tib ketiladi — bu majburiy ma'lumot emas.
    """
    info = devinfo.device_info(ip, username, password)
    if not info or not (info["model"] or info["firmware"]):
        return
    with get_db() as db:
        db.executemany(
            "UPDATE cameras SET model = ?, firmware = ? WHERE id = ?",
            [(info["model"], info["firmware"], cid) for cid in camera_ids],
        )


def _enrich_new_camera(camera_ids: list[int], ip: str, port: int,
                       username: str, password: str) -> None:
    """Yangi qo'shilgan kamera sahifasi darhol to'liq bo'lsin:
    holat hozir tekshiriladi (tez, ≤1.5 s), pasport fonda to'ladi."""
    if not ip:
        return
    health.check_now(ip, port)
    threading.Thread(
        target=_fill_passport,
        args=(camera_ids, ip, username, password),
        daemon=True,
    ).start()


# ---------- kameralar CRUD ----------

@router.get("/cameras")
def admin_list(request: Request, q: str = "", limit: int = 100, offset: int = 0):
    """Boshqaruv ro'yxati — qidiruv va sahifalash bilan.

    Kamera ko'p bo'lganda hammasini birdan yuborish ham tarmoqni, ham
    brauzerni bo'g'adi, shuning uchun bo'lib beriladi.
    """
    where, params = "", []
    if q.strip():
        needle = f"%{q.strip()}%"
        where = ("WHERE name LIKE ? OR region LIKE ? OR ip LIKE ? "
                 "OR slug LIKE ? OR note LIKE ?")
        params = [needle] * 5

    limit = max(1, min(limit, 500))
    with get_db() as db:
        total = db.execute(f"SELECT COUNT(*) FROM cameras {where}", params).fetchone()[0]
        rows = db.execute(
            f"SELECT * FROM cameras {where} ORDER BY region, name LIMIT ? OFFSET ?",
            params + [limit, max(0, offset)],
        ).fetchall()
    return {
        "total": total,
        "offset": offset,
        "cameras": [admin_camera(r, request) for r in rows],
    }


@router.post("/cameras", status_code=201)
def admin_create(cam: CameraIn, request: Request):
    cam.validate_complete()
    # Takror qo'shishdan himoya: bitta IP+port+yo'l — bitta kamera.
    # (Skan sahifasida "Qo'shish" ikki bosilsa jimgina nusxa paydo
    # bo'lardi.) Xohlagan takror ataylab bo'lsa, yo'lni o'zgartiring.
    if cam.source_type == "rtsp":
        with get_db() as db:
            dup = db.execute(
                "SELECT name FROM cameras WHERE ip = ? AND port = ? "
                "AND rtsp_path = ?",
                (cam.ip.strip(), cam.port, cam.rtsp_path.strip()),
            ).fetchone()
        if dup:
            raise HTTPException(
                409, f"Bu kamera allaqachon qo'shilgan: «{dup['name']}» "
                     "(o'sha IP, port va RTSP yo'l)")
    codec, transcode, resolution, fps = detect_codec(cam, cam.password or "")
    sub_path, sub_codec = detect_sub_path(cam, cam.password or "")
    with get_db() as db:
        slug = unique_slug(db, f"{cam.region}_{cam.name}")
        try:
            db.execute(
                "INSERT INTO cameras (name, region, lat, lng, stream_url, slug, ip, "
                "port, username, password_enc, rtsp_path, sub_path, sub_codec, "
                "vendor, enabled, "
                "note, codec, resolution, fps, transcode, always_on, node_id, external_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cam.name.strip(), cam.region.strip(), cam.lat, cam.lng,
                    cam.stream_url.strip() if cam.source_type == "manual" else "",
                    slug,
                    cam.ip.strip() if cam.source_type == "rtsp" else "",
                    cam.port, cam.username.strip(),
                    security.encrypt(cam.password) if cam.password else "",
                    cam.rtsp_path.strip(), sub_path, sub_codec, cam.vendor,
                    int(cam.enabled),
                    cam.note.strip(), codec, resolution, fps, int(transcode),
                    int(cam.always_on), cam.node_id, cam.external_id,
                ),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, f"external_id band: {cam.external_id}")
        row = db.execute("SELECT * FROM cameras WHERE slug = ?", (slug,)).fetchone()
    # Javob "Tekshirilmagan" bo'lib ketmasin: holat hozir aniqlanadi,
    # model/firmware fonda to'ladi.
    _enrich_new_camera([row["id"]], row["ip"] or "", row["port"] or 554,
                       row["username"] or "", cam.password or "")
    return admin_camera(row, request)


@router.put("/cameras/{ref}")
def admin_update(ref: str, cam: CameraIn, request: Request):
    cam.validate_complete()
    with get_db() as db:
        old = resolve_ref(db, ref)
        if old is None:
            raise HTTPException(404, "Kamera topilmadi")
        camera_id = old["id"]

        # Parol bo'sh qoldirilsa — eskisi saqlanadi.
        if cam.password:
            password_enc = security.encrypt(cam.password)
        elif cam.source_type == "rtsp":
            password_enc = old["password_enc"] or ""
        else:
            password_enc = ""

        slug = old["slug"]
        if cam.name.strip() != old["name"] or cam.region.strip() != old["region"]:
            slug = unique_slug(db, f"{cam.region}_{cam.name}", exclude_id=camera_id)

        # Kodekni qayta aniqlaymiz — kamera sozlamasi o'zgargan bo'lishi mumkin.
        password = cam.password or security.decrypt(password_enc)
        codec, transcode, resolution, fps = detect_codec(cam, password)
        responded = bool(codec)
        if not responded:                   # kamera javob bermadi — eskisi qoladi
            codec, transcode = old["codec"] or "", bool(old["transcode"])
            resolution = old["resolution"] or ""
            fps = float(old["fps"] or 0.0) if "fps" in old.keys() else 0.0

        sub_path, sub_codec = detect_sub_path(cam, password)
        if not sub_path and not responded:  # kamera javob bermadi — eskisi qoladi
            sub_path = old["sub_path"] or ""
            sub_codec = (old["sub_codec"] or "") if "sub_codec" in old.keys() else ""

        try:
            db.execute(
                "UPDATE cameras SET name=?, region=?, lat=?, lng=?, stream_url=?, "
                "slug=?, ip=?, port=?, username=?, password_enc=?, rtsp_path=?, "
                "sub_path=?, sub_codec=?, vendor=?, enabled=?, note=?, codec=?, "
                "resolution=?, fps=?, "
                "transcode=?, always_on=?, node_id=?, external_id=? WHERE id=?",
                (
                    cam.name.strip(), cam.region.strip(), cam.lat, cam.lng,
                    cam.stream_url.strip() if cam.source_type == "manual" else "",
                    slug,
                    cam.ip.strip() if cam.source_type == "rtsp" else "",
                    cam.port, cam.username.strip(), password_enc,
                    cam.rtsp_path.strip(), sub_path, sub_codec, cam.vendor,
                    int(cam.enabled),
                    cam.note.strip(), codec, resolution, fps, int(transcode),
                    int(cam.always_on), cam.node_id, cam.external_id, camera_id,
                ),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, f"external_id band: {cam.external_id}")
        row = db.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
    # Manzil/parol o'zgargan bo'lishi mumkin — holat va pasport yangilanadi.
    _enrich_new_camera([camera_id], row["ip"] or "", row["port"] or 554,
                       row["username"] or "", password)
    return admin_camera(row, request)


@router.post("/cameras/detect-sub")
def admin_detect_sub():
    """Sub yo'li yo'q kameralarga past sifatli 2-oqimni topib beradi.

    Mavjud bazani bir bosishda to'ldirish uchun: har bir kamera uchun
    ishlab chiqaruvchi shablonidan sub yo'l hosil qilinadi va parallel
    tekshiriladi — faqat javob berganlari saqlanadi.
    """
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM cameras WHERE enabled = 1 AND ip IS NOT NULL "
            "AND ip != '' AND (sub_path IS NULL OR sub_path = '')"
        ).fetchall()

    def job(row) -> tuple[int, str, str]:
        main = (row["rtsp_path"] or "").strip()
        candidate = channel_path(row["vendor"] or "boshqa",
                                 channel_from_path(main), "sub")
        if candidate == main:
            return row["id"], "", ""
        result = probe(row["ip"], row["port"] or 554, candidate,
                       row["username"] or "",
                       security.decrypt(row["password_enc"]))
        if not result.get("ok"):
            return row["id"], "", ""
        return row["id"], candidate, result.get("codec", "")

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(job, rows))

    found = [(sub, codec, cam_id) for cam_id, sub, codec in results if sub]
    if found:
        with get_db() as db:
            db.executemany(
                "UPDATE cameras SET sub_path = ?, sub_codec = ? WHERE id = ?",
                found)
    return {"checked": len(rows), "found": len(found)}


@router.delete("/cameras/{ref}", status_code=204)
def admin_delete(ref: str):
    with get_db() as db:
        row = resolve_ref(db, ref)
        if row is None:
            raise HTTPException(404, "Kamera topilmadi")
        db.execute("DELETE FROM cameras WHERE id = ?", (row["id"],))


@router.post("/cameras/{ref}/enabled")
def admin_set_enabled(ref: str, body: EnabledIn, request: Request):
    """Kamerani yoqish/o'chirib qo'yish — to'liq tahrirsiz, bir bosishda.
    O'chirilgan kameraga oqim ham, surat ham berilmaydi; reconciler
    MediaMTX yo'lini o'zi olib tashlaydi."""
    with get_db() as db:
        row = resolve_ref(db, ref)
        if row is None:
            raise HTTPException(404, "Kamera topilmadi")
        db.execute("UPDATE cameras SET enabled = ? WHERE id = ?",
                   (int(body.enabled), row["id"]))
        row = db.execute("SELECT * FROM cameras WHERE id = ?",
                         (row["id"],)).fetchone()
    return admin_camera(row, request)


@router.get("/cameras/{ref}/uptime")
def admin_uptime(ref: str, hours: int = 168):
    """Kameraning ish vaqti tarixi: qachon uzilgan/qaytgan, jami qancha
    o'chiq turgan, uptime foizi. Manba — events jadvalidagi online/offline
    o'tishlari (saqlash muddati 30 kun, shundan uzuni so'ralmaydi)."""
    hours = max(1, min(hours, 24 * 30))
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    with get_db() as db:
        row = resolve_ref(db, ref)
        if row is None:
            raise HTTPException(404, "Kamera topilmadi")
        transitions = db.execute(
            "SELECT ts, kind FROM events WHERE slug = ? "
            "AND kind IN ('online', 'offline') AND ts >= ? ORDER BY ts, id",
            (row["slug"], since.strftime("%Y-%m-%d %H:%M:%S")),
        ).fetchall()

    def parse(ts: str) -> datetime:
        # SQLite datetime('now') — UTC, lekin zonasiz satr.
        return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)

    # Davr boshidagi holat: birinchi o'tishning teskarisi; o'tish umuman
    # bo'lmasa — hozirgi holat butun davrga taalluqli.
    if transitions:
        state_at_start = ("offline" if transitions[0]["kind"] == "online"
                          else "online")
    else:
        alive = health.online(row["ip"], row["port"])
        state_at_start = "offline" if alive is False else "online"

    segments = []                      # [{state, from, to, seconds}]
    cursor, state = since, state_at_start
    for tr in transitions:
        t = parse(tr["ts"])
        if t > cursor:
            segments.append({"state": state, "from": cursor.isoformat(),
                             "to": t.isoformat(),
                             "seconds": int((t - cursor).total_seconds())})
        cursor, state = t, tr["kind"]
    segments.append({"state": state, "from": cursor.isoformat(),
                     "to": now.isoformat(),
                     "seconds": int((now - cursor).total_seconds())})

    offline_s = sum(s["seconds"] for s in segments if s["state"] == "offline")
    total_s = max(1, int((now - since).total_seconds()))
    outages = sum(1 for tr in transitions if tr["kind"] == "offline")
    last_offline = next((tr["ts"] for tr in reversed(transitions)
                         if tr["kind"] == "offline"), None)
    return {
        "hours": hours,
        "uptime_pct": round(100 * (total_s - offline_s) / total_s, 2),
        "offline_seconds": offline_s,
        "outages": outages,
        "last_offline_at": last_offline,
        "segments": segments,
        "transitions": [{"ts": tr["ts"], "kind": tr["kind"]}
                        for tr in transitions],
    }


# ---------- NVR import va skaner ----------

def parse_channels(spec: str, limit: int = 512) -> list[int]:
    """"1-16" yoki "1,3,5-8" ni raqamlar ro'yxatiga aylantiradi."""
    numbers: list[int] = []
    for chunk in spec.replace(" ", "").split(","):
        if not chunk:
            continue
        if "-" in chunk:
            try:
                start, end = (int(v) for v in chunk.split("-", 1))
            except ValueError:
                raise HTTPException(400, f"Kanal oralig'i noto'g'ri: {chunk}")
            if start > end:
                start, end = end, start
            numbers.extend(range(start, end + 1))
        else:
            try:
                numbers.append(int(chunk))
            except ValueError:
                raise HTTPException(400, f"Kanal raqami noto'g'ri: {chunk}")

    unique = sorted({n for n in numbers if n > 0})
    if not unique:
        raise HTTPException(400, "Kanallar ko'rsatilmagan")
    if len(unique) > limit:
        raise HTTPException(400, f"Bir marta ko'pi bilan {limit} ta kanal")
    return unique


def spread_point(lat: float, lng: float, index: int, spread_m: int) -> tuple[float, float]:
    """Nuqtalarni spiral bo'ylab tarqatadi — markerlar ustma-ust tushmasin."""
    if spread_m <= 0 or index == 0:
        return lat, lng
    step = math.sqrt(index) * spread_m
    angle = index * 2.399963            # oltin burchak — bir tekis tarqaladi
    d_lat = (step * math.cos(angle)) / 111_320
    d_lng = (step * math.sin(angle)) / (111_320 * max(0.2, math.cos(math.radians(lat))))
    return round(lat + d_lat, 6), round(lng + d_lng, 6)


@router.post("/nvr/import")
def admin_nvr_import(body: NvrIn):
    """Registratordagi kanallarni birdaniga kameralarga aylantiradi."""
    channels = parse_channels(body.channels)
    prefix = body.name_prefix.strip() or body.region.strip()

    planned = []
    for index, channel in enumerate(channels):
        lat, lng = spread_point(body.lat, body.lng, index, body.spread_m)
        planned.append({
            "channel": channel,
            "name": f"{prefix} {channel}-kanal",
            "rtsp_path": channel_path(body.vendor, channel, body.stream),
            # Video devor uchun past sifatli 2-oqim — faqat tekshiruvdan
            # o'tsa saqlanadi (ba'zi NVR kanallarida sub yo'q bo'ladi).
            "sub_path": (channel_path(body.vendor, channel, "sub")
                         if body.stream == "main" else ""),
            "lat": lat, "lng": lng,
        })

    # Tekshirish parallel ketadi — 64 ta kanalni ketma-ket tekshirish
    # bir necha daqiqa oladi, parallel esa bir necha soniya. Asosiy va
    # sub oqimlar bitta hovuzda birga tekshiriladi.
    results: dict[tuple[int, str], dict] = {}
    if body.probe:
        jobs = [(item["channel"], "main", item["rtsp_path"]) for item in planned]
        jobs += [(item["channel"], "sub", item["sub_path"])
                 for item in planned if item["sub_path"]]
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = {
                pool.submit(probe, body.ip, body.port, path,
                            body.username, body.password): (channel, kind)
                for channel, kind, path in jobs
            }
            for future in as_completed(futures):
                results[futures[future]] = future.result()

    for item in planned:
        result = results.get((item["channel"], "main"))
        item["ok"] = result["ok"] if result else None
        item["codec"] = result.get("codec", "") if result else ""
        item["resolution"] = result.get("resolution", "") if result else ""
        item["transcode"] = bool(result.get("needs_transcode")) if result else False
        item["message"] = result["message"] if result else "tekshirilmadi"
        # Sub oqim kodegi alohida saqlanadi: devor plitkasi sub'ni
        # ko'rsatib turib "H265" yorlig'ini chizmasin (asosiy oqim H.265,
        # sub esa deyarli doim H.264 bo'ladi).
        item["sub_codec"] = ""
        if body.probe:
            sub = results.get((item["channel"], "sub"))
            if sub and sub.get("ok"):
                item["sub_codec"] = sub.get("codec", "")
            else:
                item["sub_path"] = ""

    if body.dry_run:
        return {"planned": planned, "created": 0,
                "reachable": sum(1 for p in planned if p["ok"])}

    # Javob bermagan kanallar saqlanmaydi — NVR'da bo'sh slotlar ko'p bo'ladi.
    keep = [p for p in planned if p["ok"] or not body.probe]
    password_enc = security.encrypt(body.password) if body.password else ""
    created = 0
    created_ids: list[int] = []
    with get_db() as db:
        for item in keep:
            # Takror kanal (o'sha IP+port+yo'l) qayta saqlanmaydi.
            if db.execute(
                "SELECT 1 FROM cameras WHERE ip = ? AND port = ? "
                "AND rtsp_path = ?",
                (body.ip.strip(), body.port, item["rtsp_path"]),
            ).fetchone():
                item["message"] = "allaqachon qo'shilgan — o'tkazib yuborildi"
                continue
            slug = unique_slug(db, f"{body.region}_{item['name']}")
            cur = db.execute(
                "INSERT INTO cameras (name, region, lat, lng, stream_url, slug, ip, "
                "port, username, password_enc, rtsp_path, sub_path, sub_codec, "
                "vendor, enabled, "
                "note, codec, resolution, transcode, always_on, node_id) "
                "VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
                (item["name"], body.region.strip(), item["lat"], item["lng"], slug,
                 body.ip.strip(), body.port, body.username.strip(), password_enc,
                 item["rtsp_path"], item["sub_path"], item["sub_codec"],
                 body.vendor, int(body.enabled),
                 f"{body.ip} · {item['channel']}-kanal",
                 item["codec"], item["resolution"], int(item["transcode"]),
                 body.node_id),
            )
            created_ids.append(cur.lastrowid)
            created += 1

    # NVR manzili bitta — holat bir tekshiruvda, pasport fonda to'ladi.
    if created_ids:
        _enrich_new_camera(created_ids, body.ip.strip(), body.port,
                           body.username.strip(), body.password)

    return {"planned": planned, "created": created,
            "skipped": len(planned) - created,
            "reachable": sum(1 for p in planned if p["ok"])}


def _probe_many(ip: str, port: int, jobs: dict, username: str,
                password: str) -> dict:
    """Bir nechta RTSP yo'lni parallel tekshiradi: {kalit: probe natijasi}."""
    results: dict = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {
            pool.submit(probe, ip, port, path, username, password): key
            for key, path in jobs.items()
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return results


@router.post("/scan")
def admin_scan(body: ScanIn):
    """Qurilmani o'zi aniqlaydi: turi (kamera/NVR), shabloni va jonli kanallari.

    Oddiy foydalanuvchi RTSP yo'lini ham, kanal raqamlarini ham bilmaydi —
    IP va login/parol yetarli. Skaner mashhur shablonlarni sinab, qaysi
    biri ishlashini topadi, so'ng kanallarni 8 talik bloklarda tekshiradi
    va bo'sh blok kelganda to'xtaydi.
    """
    ip, port = body.ip.strip(), body.port
    user, pw = body.username.strip(), body.password
    if not pw and body.camera_id:
        with get_db() as db:
            row = db.execute("SELECT password_enc FROM cameras WHERE id = ?",
                             (body.camera_id,)).fetchone()
        if row:
            pw = security.decrypt(row["password_enc"])

    # 1) Shablonni aniqlash — har bir ishlab chiqaruvchining 1-kanali.
    candidates = {v: channel_path(v, 1, "main") for v in CHANNEL_VENDORS}
    candidates["boshqa"] = "/stream1"          # bitta oqimli oddiy kameralar
    first = _probe_many(ip, port, candidates, user, pw)

    # Hech biri ochilmadi-yu, parol xatosi bor — avval shuni aytamiz.
    if not any(r["ok"] for r in first.values()):
        for stage in ("parol", "oqim", "rtsp", "tarmoq"):
            hit = next((r for r in first.values() if r.get("stage") == stage), None)
            if hit:
                return {"found": False, "message": hit["message"]}
        return {"found": False, "message": "Qurilma javob bermadi"}

    vendor = next(v for v in [*CHANNEL_VENDORS, "boshqa"]
                  if first.get(v, {}).get("ok"))
    vendor_name = next((v["name"] for v in VENDORS if v["id"] == vendor), vendor)

    def entry(channel: int, result: dict) -> dict:
        return {
            "channel": channel,
            "rtsp_path": channel_path(vendor, channel, "main"),
            "codec": result.get("codec", ""),
            "needs_transcode": bool(result.get("needs_transcode")),
        }

    channels = [entry(1, first[vendor])]

    # 2) Kanallarni sanash — faqat kanal raqamini biladigan shablonlarda.
    if vendor != "boshqa":
        start = 2
        while start <= body.max_channels:
            block = range(start, min(start + 8, body.max_channels + 1))
            jobs = {c: channel_path(vendor, c, "main") for c in block}
            results = _probe_many(ip, port, jobs, user, pw)
            live = [c for c in sorted(results) if results[c]["ok"]]
            channels.extend(entry(c, results[c]) for c in live)
            if not live:                       # bo'sh blok — qurilma tugadi
                break
            start += 8

    return {
        "found": True,
        "vendor": vendor,
        "vendor_name": vendor_name,
        "device": "nvr" if len(channels) > 1 else "camera",
        "channels": channels,
    }


@router.post("/probe")
def admin_probe(body: ProbeIn):
    """Kamera bilan aloqani va login/parolni tekshiradi."""
    password = body.password or ""
    if not password and body.camera_id:
        with get_db() as db:
            row = db.execute(
                "SELECT password_enc FROM cameras WHERE id = ?", (body.camera_id,)
            ).fetchone()
        if row:
            password = security.decrypt(row["password_enc"])
    return probe(body.ip.strip(), body.port, body.rtsp_path.strip(),
                 body.username.strip(), password)


# ---------- foydalanuvchilar ----------
#
# Rollar: 'admin' — hammasini boshqaradi (shu bo'lim ham faqat unga ochiq);
# 'operator' — faqat o'ziga biriktirilgan hududlardagi kameralarni ko'radi
# (xarita ro'yxati, oqim va surat shu ro'yxat bilan cheklanadi).

def _user_view(db, row) -> dict:
    return {
        "id": row["id"], "username": row["username"], "role": row["role"],
        "created_at": row["created_at"],
        "regions": [],     # rollar/hududlar asosiy tizimda — bu yerda yo'q
    }


def _check_password(password: str | None, required: bool) -> None:
    if required and not password:
        raise HTTPException(400, "Parol kiritilmagan")
    if password and len(password) < 6:
        raise HTTPException(400, "Parol kamida 6 belgidan iborat bo'lsin")


@router.get("/users")
def admin_users():
    with get_db() as db:
        rows = db.execute(
            "SELECT id, username, role, created_at FROM admins ORDER BY id"
        ).fetchall()
        return {"users": [_user_view(db, r) for r in rows]}


@router.post("/users", status_code=201)
def admin_user_create(body: UserIn):
    _check_password(body.password, required=True)
    pw_hash, salt = security.hash_password(body.password)
    with get_db() as db:
        try:
            cur = db.execute(
                "INSERT INTO admins (username, pw_hash, pw_salt, role) "
                "VALUES (?, ?, ?, 'admin')",
                (body.username.strip(), pw_hash, salt),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(400, "Bunday login allaqachon bor")
        row = db.execute("SELECT id, username, role, created_at FROM admins "
                         "WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _user_view(db, row)


@router.put("/users/{user_id}")
def admin_user_update(user_id: int, body: UserIn):
    _check_password(body.password, required=False)
    with get_db() as db:
        old = db.execute("SELECT * FROM admins WHERE id = ?",
                         (user_id,)).fetchone()
        if old is None:
            raise HTTPException(404, "Foydalanuvchi topilmadi")
        try:
            db.execute("UPDATE admins SET username = ? WHERE id = ?",
                       (body.username.strip(), user_id))
        except sqlite3.IntegrityError:
            raise HTTPException(400, "Bunday login allaqachon bor")
        if body.password:
            pw_hash, salt = security.hash_password(body.password)
            db.execute("UPDATE admins SET pw_hash = ?, pw_salt = ? WHERE id = ?",
                       (pw_hash, salt, user_id))
            # Parol almashdi — eski sessiyalar bekor.
            db.execute("DELETE FROM sessions WHERE admin_id = ?", (user_id,))
        row = db.execute("SELECT id, username, role, created_at FROM admins "
                         "WHERE id = ?", (user_id,)).fetchone()
        return _user_view(db, row)


@router.delete("/users/{user_id}", status_code=204)
def admin_user_delete(user_id: int, me=Depends(require_admin)):
    if me["id"] == user_id:
        raise HTTPException(400, "O'z hisobingizni o'chira olmaysiz")
    with get_db() as db:
        row = db.execute("SELECT role FROM admins WHERE id = ?",
                         (user_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Foydalanuvchi topilmadi")
        if db.execute("SELECT COUNT(*) FROM admins").fetchone()[0] <= 1:
            raise HTTPException(400, "Oxirgi admin o'chirilmaydi")
        db.execute("DELETE FROM admins WHERE id = ?", (user_id,))
        db.execute("DELETE FROM sessions WHERE admin_id = ?", (user_id,))


# ---------- MediaMTX ----------

@router.get("/runtime")
def admin_runtime():
    """Faol yo'llarning jonli xaritasi: slug -> ready/readers/baytlar.

    Servis konsoli shu orqali kamera kesimida kirish tezligini (ikki
    so'rov orasidagi bayt farqidan) va tomoshabinlar sonini chizadi.
    Faqat lokal tugun — uzoq tugunlar salomatligi /admin/nodes da.
    """
    paths = mediamtx_sync.list_active_paths()
    if paths is None:
        return {"mediamtx": False, "paths": {}}
    return {"mediamtx": True, "paths": {
        name: {
            "ready": bool(item.get("ready")),
            "readers": len(item.get("readers") or []),
            "bytes_received": int(item.get("bytesReceived") or 0),
            "bytes_sent": int(item.get("bytesSent") or 0),
            "warm": mediamtx_sync.is_warm(name),
        } for name, item in paths.items()
    }}


@router.post("/cameras/{ref}/keyframe")
def admin_keyframe(ref: str, stream: str = "main"):
    """Kameradan darhol keyframe so'raydi (ONVIF/ISAPI) — diagnostika amali.

    `sent: true` — kamera qabul qildi; `false` — qo'llamaydi yoki 2 soniya
    ichida takror so'rov (bosim himoyasi).
    """
    with get_db() as db:
        row = resolve_ref(db, ref)
    if row is None:
        raise HTTPException(404, "Kamera topilmadi")
    if not row["ip"]:
        raise HTTPException(400, "Bu kamera tayyor oqim — keyframe so'ralmaydi")
    sent = fast_start.request_keyframe(
        row["ip"], row["username"] or "",
        security.decrypt(row["password_enc"]),
        row["rtsp_path"] or "", row["vendor"] or "",
        stream="sub" if stream == "sub" else "main")
    return {"sent": bool(sent)}


def _dir_size_mb(path) -> tuple[float, int]:
    total, count = 0, 0
    try:
        for f in path.glob("*"):
            try:
                total += f.stat().st_size
                count += 1
            except OSError:
                pass
    except OSError:
        pass
    return round(total / 1_048_576, 1), count


@router.get("/status")
def admin_status():
    """Tizim salomatligi bir qarashda — 5000 kamerani ko'z bilan emas,
    raqam bilan kuzatish uchun: MediaMTX tirikmi, health sweep intervalga
    sig'ayaptimi, qaysi faol oqimlar muzlagan, tugunlar qay ahvolda."""
    with get_db() as db:
        node_rows = db.execute(
            "SELECT id, name, api_base FROM nodes WHERE enabled = 1 ORDER BY id"
        ).fetchall()
    nodes = []
    for row in node_rows:
        runtime = mediamtx_sync.node_runtime(row["api_base"])
        stalled = reconciler.stalled_count(row["id"])
        nodes.append({
            "name": row["name"],
            "status": ("offline" if runtime is None else
                       "degraded" if stalled else "online"),
            "ready": runtime["ready"] if runtime else 0,
            "readers": runtime["readers"] if runtime else 0,
            "stalled": stalled,
            # 0 bo'lishi kerak. Noldan katta bo'lsa MediaMTX'da eski
            # versiyadan qolgan ortiqcha yo'llar bor va ular tozalanmoqda —
            # shu davrda kameralar sekinroq ochiladi (jurnalda ko'rsatma).
            "pending_paths": reconciler.pending_count(row["id"]),
        })
    from core import snapshots
    from core.db import DB_PATH
    from core.log import LOG_PATH
    db_mb = round(DB_PATH.stat().st_size / 1_048_576, 1) if DB_PATH.exists() else 0
    log_mb = round(LOG_PATH.stat().st_size / 1_048_576, 1) if LOG_PATH.exists() else 0
    snap_mb, snap_files = _dir_size_mb(snapshots.SNAP_DIR)
    return {
        "mediamtx": mediamtx_sync.api_available(),
        "health": health.sweep_stats(),
        "disk": {"snapshots_mb": snap_mb, "snapshots_files": snap_files,
                 "db_mb": db_mb, "log_mb": log_mb},
        "stalled": sorted(reconciler.stalled_paths()),
        "nodes": nodes,
    }


@router.get("/events")
def admin_events(limit: int = 100):
    """Media qatlamining so'nggi hodisalari: oqim muzladi/tiklandi,
    MediaMTX qayta ishga tushdi. Jonli holat o'zgarishlari SSE'da (/events)."""
    with get_db() as db:
        rows = db.execute(
            "SELECT ts, kind, ip, port, slug, detail FROM events "
            "ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),),
        ).fetchall()
    return {"events": [dict(r) for r in rows]}


@router.post("/mediamtx/sync")
def admin_sync():
    """mediamtx.yml faylini qayta yozadi va har bir tugunni jonli yangilaydi.

    Kameralar tugun bo'yicha ajratib yuboriladi — aks holda boshqa tugunga
    biriktirilgan kameralar lokal MediaMTX'ga ham tushib, 30 soniyadan
    keyin reconciler ularni qaytarib o'chirardi (keraksiz tebranish).
    """
    with get_db() as db:
        cameras = cameras_for_mediamtx(db)
        nodes = [dict(r) for r in db.execute(
            "SELECT * FROM nodes WHERE enabled = 1 ORDER BY id").fetchall()]
    written = mediamtx_sync.write_config(cameras)
    if not nodes:
        nodes = [{"id": 1, "name": "Asosiy", "api_base": None}]

    results = []
    for node in nodes:
        node_cams = [c for c in cameras if (c.get("node_id") or 1) == node["id"]]
        pushed = mediamtx_sync.push_to_api(node_cams, api_base=node["api_base"])
        results.append({"node": node["name"], **pushed})

    ok = all(r["ok"] for r in results)
    message = (results[0]["message"] if len(results) == 1 else
               " · ".join(f"{r['node']}: {r['message']}" for r in results))
    return {
        "written": written,
        "config_path": str(mediamtx_sync.CONFIG_PATH),
        "live": {
            "ok": ok, "message": message,
            "added": sum(r["added"] for r in results),
            "updated": sum(r["updated"] for r in results),
            "removed": sum(r["removed"] for r in results),
            "nodes": results,
        },
    }


@router.get("/mediamtx/config")
def admin_config_preview():
    with get_db() as db:
        cameras = cameras_for_mediamtx(db)
    return {
        # Parollar yashiriladi — faylga esa ochiq holda yoziladi (MediaMTX uchun).
        "text": mask_config(mediamtx_sync.build_config(cameras)),
        "api_available": mediamtx_sync.api_available(),
        "transcoding": sum(1 for c in cameras if c["transcode"] and c["enabled"]),
        "gpu": mediamtx_sync.has_nvenc(),
    }

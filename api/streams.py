"""Nigoh — batch oqim chiptalari: bitta so'rovda ko'p kamera.

Video devor 64 katakni alohida so'rov bilan ochsa, 64 HTTP aylanish
bo'ladi. `POST /streams` bitta so'rovda hammasini qaytaradi: har id
uchun ruxsat va holat alohida hisoblanadi — bittasi yiqilsa qolgani
ishlayveradi. Yo'llarni MediaMTX'da tayyorlash parallel ketadi.
"""
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core import fast_start
from core.db import get_db
from media import sync as mediamtx_sync

from .helpers import (
    camera_for_mediamtx,
    camera_state,
    node_info,
    resolve_ref,
    stream_urls,
)

# Prefiks nisbiy — create_app uni /api/v1 (asosiy) va /api (eski) ostida ulaydi.
router = APIRouter(prefix="/streams", tags=["streams"])

MAX_IDS = 128

# So'rovlar orasida qayta ishlatiladigan bitta hovuz — har chaqiruvda
# 16 ta yangi thread ochib-yopish shart emas.
_pool = ThreadPoolExecutor(max_workers=16, thread_name_prefix="streams")

# O'rtacha bitrate taxmini (Mbit/s) — egress bahosi uchun. Aniq o'lchov
# emas: sub oqimlar ~0,5, asosiy H.264 1080p ~4. Haqiqiy egress /health
# ko'rsatkichida bo'ladi (reja 4.4).
_SUB_MBPS = 0.5
_MAIN_MBPS = 4.0


class StreamsIn(BaseModel):
    # Har biri ichki id (12) yoki tashqi ref ("ext:cam-014") bo'la oladi.
    ids: list[int | str] = Field(min_length=1, max_length=MAX_IDS)
    quality: str = ""            # "" — asosiy oqim, "sub" — past sifat
    hevc: bool = False           # brauzer H.265 ni o'zi o'qiy oladimi


@router.post("")
def batch_streams(body: StreamsIn, request: Request):
    """Bir nechta kameraning oqim manzillari bitta so'rovda.

    Javob `streams` lug'ati so'ralgan id bilan kalitlanadi. Muvaffaqiyat:
    `{webrtc, hls, poster, mode}`; muammo: `{error}` — `topilmadi`,
    `disabled`, `offline`. `egress_estimate_mbps` — hammasi birdan
    ko'rilsa serverdan chiqadigan taxminiy trafik.
    """
    if body.quality not in ("", "sub"):
        raise HTTPException(400, "quality faqat '' yoki 'sub' bo'lishi mumkin")

    with get_db() as db:
        rows = {str(ref): resolve_ref(db, str(ref)) for ref in body.ids}

    def job(item):
        key, row = item
        if row is None:
            return key, {"error": "topilmadi"}, 0.0
        state = camera_state(row)
        if state in ("disabled", "offline"):
            return key, {"error": state}, 0.0

        camera = camera_for_mediamtx(row)
        if camera:
            node = node_info(camera["node_id"])
            api_base = node["api_base"] if node else None
            sub = (mediamtx_sync.sub_variant(camera)
                   if body.quality == "sub" else None)
            if sub:
                # Issiq to'plam: keyingi 10 daqiqada qayta ochilish < 1 s.
                mediamtx_sync.mark_warm(sub["slug"])
                mediamtx_sync.ensure_path(sub, api_base)
            else:
                mediamtx_sync.ensure_path(camera, api_base)
                if api_base is None:           # o'girish faqat lokal tugunda
                    mediamtx_sync.ensure_transcode_path(camera)
            fast_start.request_keyframe_async(
                camera["ip"], camera["username"], camera["password"],
                camera["rtsp_path"], row["vendor"] or "",
                stream="sub" if sub else "main")

        urls = stream_urls(row, request, hevc_ok=body.hevc,
                           quality=body.quality)
        mbps = _SUB_MBPS if urls.get("mode") == "sub" else _MAIN_MBPS
        return key, {
            "webrtc": urls.get("webrtc_url") or "",
            "hls": urls.get("stream_url") or "",
            "poster": f"/api/v1/cameras/{row['id']}/snapshot",
            "mode": urls.get("mode", ""),
        }, mbps

    def safe_job(item):
        # Bitta kameradagi kutilmagan xato qolganlarini yiqitmasin.
        try:
            return job(item)
        except Exception as exc:               # noqa: BLE001
            return item[0], {"error": f"ichki xato: {exc.__class__.__name__}"}, 0.0

    results = list(_pool.map(safe_job, rows.items()))
    egress = sum(mbps for _, _, mbps in results)
    return {
        "streams": {key: data for key, data, _ in results},
        "egress_estimate_mbps": round(egress, 1),
    }

"""Nigoh — servis salomatligi bitta so'rovda: GET /health.

Kalitsiz — Docker HEALTHCHECK va yuk balanslagichlar uchun. Ichida sir
yo'q: hisoblagichlar va holat, xolos.

Egress (serverdan chiqayotgan video trafigi) shu yerda ko'rinadi, chunki
ko'payish nuqtasi serverda: 100 tomoshabin bitta kamerani ochsa, kamera
bitta oqim beradi, server 100 ta chiqaradi. Sun'iy chegara qo'yilmaydi —
faqat ko'rinadigan qilinadi (devor 80% da sariq, 95% da qizil).
"""
import os
import threading
import time

from fastapi import APIRouter

from core import bus, health, metrics, snapshots
from media import sync as mediamtx_sync

router = APIRouter(tags=["health"])

# Tarmoq kartasining sig'imi (Mbit/s) — egress foizini hisoblash uchun.
NIC_CAPACITY_MBPS = int(os.environ.get("NIC_CAPACITY_MBPS", "1000"))

# bytes_sent yig'ma hisob — tezlik ikki o'lchov orasidagi farqdan chiqadi.
_last = {"t": 0.0, "sent": 0}
_last_lock = threading.Lock()


def _egress_mbps(bytes_sent: int) -> float:
    """Oxirgi ikki /health chaqiruvi orasidagi o'rtacha chiqish tezligi.

    Birinchi chaqiruvda (yoki MediaMTX qayta ishga tushib hisob nolga
    qaytganda) 0 qaytadi — keyingisidan boshlab haqiqiy qiymat.
    """
    now = time.monotonic()
    with _last_lock:
        t0, s0 = _last["t"], _last["sent"]
        _last["t"], _last["sent"] = now, bytes_sent
    if not t0 or now <= t0 or bytes_sent < s0:
        return 0.0
    return round((bytes_sent - s0) * 8 / (now - t0) / 1_000_000, 1)


@router.get("/health")
def service_health():
    runtime = mediamtx_sync.node_runtime()
    egress = _egress_mbps(runtime["bytes_sent"]) if runtime else 0.0
    return {
        "ok": runtime is not None,
        "mediamtx": runtime is not None,
        "health": health.sweep_stats(),
        "egress_mbps": egress,
        "egress_capacity_mbps": NIC_CAPACITY_MBPS,
        "streams": runtime["ready"] if runtime else 0,
        "readers": runtime["readers"] if runtime else 0,
        "warm": mediamtx_sync.warm_count(),
        # Ayni damda MediaMTX'da ro'yxatda turgan yo'llar. Kameralar
        # soniga emas, ko'rilayotganlar soniga bog'liq — 5000 kamerada
        # ham bu son o'nlarcha bo'lib qolishi kerak.
        "managed": mediamtx_sync.managed_count(),
        "sse_subscribers": bus.subscriber_count(),
        "snapshots": snapshots.cycle_stats(),
        # Ochilish vaqti — pleyer o'lchaydi (POST /metrics/open), bu yerda
        # transport kesimida p50/p95. Qaysi bosqich sekinligi ko'rinadi:
        # stream_ms (backend/MediaMTX), signal_ms (tarmoq), frame_ms
        # (kameradagi keyframe oralig'i).
        "open_ms": metrics.summary(),
    }

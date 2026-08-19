"""Nigoh — kameralarning tirikligini fonda kuzatish.

Talab bo'yicha ulanish dizaynining bitta kamchiligi bor: kamera o'chib
qolsa, buni faqat kimdir bosganda bilamiz. Bu modul fonda yengil tekshiruv
yuritadi — xaritada o'chiq kameralar qizil nuqta bo'lib ko'rinadi.

Tekshiruv arzon bo'lishi uchun:

  * RTSP oqim ochilmaydi — faqat TCP ulanish sinaladi (kamera/registrator
    portga javob beryaptimi). Bu bir necha millisekund va trafik deyarli nol.
  * Takrorlanuvchi manzillar birlashtiriladi: 2000 kamera 40 ta NVR'da
    bo'lsa, 2000 emas, 40 ta tekshiruv ketadi.
  * Hammasi parallel (manzillar soniga moslashadi), har 60 soniyada bir marta.
"""
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from . import stats
from .db import get_db
from .log import log

CHECK_INTERVAL = 60.0   # soniya — har qancha kamerada ham yetarli
TIMEOUT = 1.5
# Ishchilar soni manzillar soniga moslashadi: 5000 kamera minglab alohida
# manzil bo'lsa ham sweep intervalga sig'adi (eng yomon holat — hammasi
# o'chiq: 3000 manzil / 256 ishchi × 1,5 s ≈ 18 s).
MAX_WORKERS = 256

_statuses: dict[tuple[str, int], bool] = {}
_stats = {"checked": 0, "online": 0, "duration_ms": 0, "at": ""}
_lock = threading.Lock()
_started = False


def _tcp_ok(pair: tuple[str, int]) -> bool:
    try:
        sock = socket.create_connection(pair, timeout=TIMEOUT)
        sock.close()
        return True
    except OSError:
        return False


def _sweep() -> None:
    started = time.monotonic()
    with get_db() as db:
        rows = db.execute(
            "SELECT DISTINCT ip, port FROM cameras "
            "WHERE enabled = 1 AND ip IS NOT NULL AND ip != ''"
        ).fetchall()
    pairs = [(row["ip"], row["port"] or 554) for row in rows]
    if not pairs:
        with _lock:
            _statuses.clear()
        return

    workers = min(MAX_WORKERS, max(8, len(pairs)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        results = list(pool.map(_tcp_ok, pairs))

    fresh = dict(zip(pairs, results))

    # Tirik chiqqanlarning "oxirgi onlayn" vaqti bazaga yoziladi — server
    # qayta ishga tushsa ham tarix yo'qolmaydi.
    alive = [pair for pair, ok in fresh.items() if ok]
    if alive:
        now_iso = datetime.now(timezone.utc).isoformat()
        with get_db() as db:
            db.executemany(
                "UPDATE cameras SET last_seen = ? WHERE ip = ? AND port = ?",
                [(now_iso, ip, port) for ip, port in alive],
            )

    # Dashboard tarixiga yoziladi: hudud kesimidagi suratlar va
    # uzildi/ulandi hodisalari. Yozilmasa ham kuzatuv to'xtamaydi.
    try:
        stats.record_sweep(fresh)
    except Exception as exc:
        log("health", "stats_write_failed", level="error", error=str(exc))

    with _lock:
        _statuses.clear()               # o'chirilgan manzillar chiqib ketadi
        _statuses.update(fresh)
        _stats.update(
            checked=len(pairs), online=sum(results),
            duration_ms=int((time.monotonic() - started) * 1000),
            at=datetime.now(timezone.utc).isoformat(),
        )


def sweep_stats() -> dict:
    """Oxirgi sweep haqida: nechta manzil, nechtasi tirik, qancha vaqt oldi.

    5000 kamerada sweep intervalga sig'ayotganini kuzatish uchun —
    `duration_ms` CHECK_INTERVAL'ga yaqinlashsa, MAX_WORKERS'ni oshirish
    yoki intervalni kengaytirish kerak.
    """
    with _lock:
        return dict(_stats)


def _loop() -> None:
    while True:
        try:
            _sweep()
        except Exception as exc:        # kuzatuv hech qachon yiqilmasin
            log("health", "sweep_failed", level="error", error=str(exc))
        time.sleep(CHECK_INTERVAL)


def start() -> None:
    """Fon tekshiruvini ishga tushiradi (bir marta)."""
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, daemon=True).start()


def online(ip: str | None, port: int | None) -> bool | None:
    """True — tirik, False — o'chiq, None — hali noma'lum yoki IP'siz."""
    if not ip:
        return None
    with _lock:
        return _statuses.get((ip, port or 554))

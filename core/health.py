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
from typing import Callable

from . import bus, events
from .db import get_db
from .log import log

CHECK_INTERVAL = 60.0   # soniya — har qancha kamerada ham yetarli
TIMEOUT = 1.5
# Timeout — noaniq javob: kamera o'chgan ham, shunchaki band ham bo'lishi
# mumkin (oqim ketayotganda qo'l berish sekinlashadi). Rad etilgan
# ulanish esa aniq javob — u yerda qayta urinishning ma'nosi yo'q.
# Shuning uchun faqat timeout'dan keyin bir marta, uzunroq muddat bilan
# qayta uriniladi: bitta sekin javob kamerani "o'chgan" qilib qo'ymasin.
RETRY_TIMEOUT = 4.0
# Ishchilar soni manzillar soniga moslashadi: 5000 kamera minglab alohida
# manzil bo'lsa ham sweep intervalga sig'adi (eng yomon holat — hammasi
# o'chiq: 3000 manzil / 256 ishchi × 1,5 s ≈ 18 s).
MAX_WORKERS = 256

_statuses: dict[tuple[str, int], bool] = {}
_stats = {"checked": 0, "online": 0, "duration_ms": 0, "at": ""}
_lock = threading.Lock()
_started = False


# Oqim olayotgan manzillarni beradigan funksiya — ilova qatlami
# o'rnatadi (`set_streaming_probe`). core/ media/ ga bog'lanmasligi
# uchun tashqaridan beriladi; reconciler'dagi `load_cameras` bilan bir
# xil naqsh.
_streaming_pairs: Callable[[], set[tuple[str, int]]] | None = None


def set_streaming_probe(fn: Callable[[], set[tuple[str, int]]]) -> None:
    global _streaming_pairs
    _streaming_pairs = fn


def _connect(pair: tuple[str, int], timeout: float) -> tuple[bool, str]:
    """(muvaffaqiyat, sabab). Sabab: "" | "timeout" | "refused"."""
    try:
        socket.create_connection(pair, timeout=timeout).close()
        return True, ""
    except TimeoutError:
        return False, "timeout"
    except OSError:
        return False, "refused"


def _tcp_ok(pair: tuple[str, int]) -> bool:
    ok, why = _connect(pair, TIMEOUT)
    if ok or why != "timeout":
        return ok
    return _connect(pair, RETRY_TIMEOUT)[0]


def _rescue_streaming(fresh: dict[tuple[str, int], bool]) -> list:
    """Oqim ketayotgan manzilni "o'chiq" deb belgilamaydi.

    TCP tekshiruvi va MediaMTX ikki mustaqil dalil, va ular teng emas:
    MediaMTX kameradan BAYT olayotgan bo'lsa, kamera tirikligi
    isbotlangan. Tekshiruv esa qurilma band, sekin yoki yangi ulanishni
    rad etayotgan paytda ham yiqiladi — shunda video ekranda ketaverib,
    yorliq "o'chgan" bo'lib turardi.

    Zaif dalil kuchlisini bekor qila olmasligi kerak.
    """
    failed = [pair for pair, ok in fresh.items() if not ok]
    if not failed or _streaming_pairs is None:
        return []
    try:
        live = _streaming_pairs()
    except Exception as exc:                # noqa: BLE001
        log("health", "streaming_probe_failed", level="warning", error=str(exc))
        return []
    rescued = [pair for pair in failed if pair in live]
    for pair in rescued:
        fresh[pair] = True
    if rescued:
        log("health", "tcp_failed_but_streaming", level="warning",
            addresses=len(rescued),
            detail="TCP tekshiruvi yiqildi, lekin MediaMTX shu manzildan "
                   "oqim olyapti — kamera tirik deb hisoblandi")
    return rescued


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
    _rescue_streaming(fresh)

    # Holat o'zgarganlarni SSE abonentlariga e'lon qilamiz. Birinchi sweep
    # (eski qiymat yo'q) e'lon qilinmaydi — ulanish paytidagi boshlang'ich
    # holat /cameras/status dan olinadi, hodisa faqat o'zgarish demak.
    with _lock:
        old = dict(_statuses)
    changed = [pair for pair, ok in fresh.items()
               if pair in old and old[pair] != ok]
    if changed:
        _publish_changes(changed, fresh)

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

    with _lock:
        _statuses.clear()               # o'chirilgan manzillar chiqib ketadi
        _statuses.update(fresh)
        _stats.update(
            checked=len(pairs), online=sum(1 for ok in fresh.values() if ok),
            duration_ms=int((time.monotonic() - started) * 1000),
            at=datetime.now(timezone.utc).isoformat(),
        )


def _publish_changes(changed: list[tuple[str, int]],
                     fresh: dict[tuple[str, int], bool]) -> None:
    """O'zgargan manzillardagi kameralarni SSE'ga uzatadi.

    Bitta NVR manzili ortida o'nlab kamera bo'lishi mumkin — hodisa
    kamera kesimida (id/external_id bilan) beriladi.
    """
    with get_db() as db:
        rows = db.execute(
            "SELECT id, external_id, slug, ip, port FROM cameras "
            "WHERE enabled = 1 AND ip IS NOT NULL AND ip != ''"
        ).fetchall()
    by_pair: dict[tuple[str, int], list] = {}
    for row in rows:
        by_pair.setdefault((row["ip"], row["port"] or 554), []).append(row)

    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    # O'tishlar bazaga ham yoziladi — kamera qachon/qancha o'chiq turgani
    # tarixи shu yozuvlardan hisoblanadi (uptime statistikasi).
    with get_db() as db:
        for pair in changed:
            state = "online" if fresh[pair] else "offline"
            for row in by_pair.get(pair, []):
                events.add(db, state, ip=pair[0], port=pair[1],
                           slug=row["slug"])
                bus.publish("state", {
                    "id": row["id"],
                    "external_id": row["external_id"] or "",
                    "state": state,
                    "at": at,
                })


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


def check_now(ip: str | None, port: int | None) -> bool | None:
    """Bitta manzilni darhol tekshiradi — yangi kamera navbatdagi sweep'ni
    (60 s gacha) kutib "Tekshirilmagan" bo'lib turmasin. Natija umumiy
    xaritaga yoziladi, tirik chiqsa last_seen ham yangilanadi."""
    if not ip:
        return None
    pair = (ip, port or 554)
    ok = _tcp_ok(pair)
    if not ok:
        probe = {pair: False}
        if _rescue_streaming(probe):
            ok = True
    with _lock:
        old = _statuses.get(pair)
        _statuses[pair] = ok
    with get_db() as db:
        # O'tish shu yerda yuz berdi — sweep endi ko'rmaydi, tarixga o'zimiz
        # yozamiz VA SSE'ga e'lon qilamiz. E'lon qilinmasa yangi qo'shilgan
        # yoki tahrirlangan kameraning birinchi holati abonentlarga
        # yetmasdi (_enrich_new_camera aynan shu yo'ldan yuradi), ya'ni
        # asosiy tizim keyingi sweep'gacha (60 s) eski holatni ko'rardi.
        if old is not None and old != ok:
            state = "online" if ok else "offline"
            at = datetime.now(timezone.utc).isoformat(timespec="seconds")
            for row in db.execute(
                "SELECT id, external_id, slug FROM cameras "
                "WHERE ip = ? AND port = ?", pair
            ).fetchall():
                events.add(db, state, ip=pair[0], port=pair[1], slug=row["slug"])
                bus.publish("state", {
                    "id": row["id"],
                    "external_id": row["external_id"] or "",
                    "state": state,
                    "at": at,
                })
        if ok:
            db.execute(
                "UPDATE cameras SET last_seen = ? WHERE ip = ? AND port = ?",
                (datetime.now(timezone.utc).isoformat(), pair[0], pair[1]),
            )
    return ok

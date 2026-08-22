"""Nigoh — MediaMTX tugunlarini tirik tutuvchi fon vazifasi (media paketi).

Har 30 soniyada, har bir yoqilgan tugun (nodes jadvali) uchun:

  1. Lokal MediaMTX yiqilgan bo'lsa — qayta ishga tushiriladi. Uzoq
     tugundagi jarayonga aralasha olmaymiz — u yiqilsa faqat hodisa
     yoziladi (API javob bermayapti). `MEDIAMTX_AUTOSTART=0` — o'chirish.

  2. Tugun yo'llari kerakli holat bilan kelishtiriladi (`push_to_api`).
     MediaMTX qayta ishga tushganda API orqali qo'shilgan yo'llar
     yo'qoladi — shu yerda o'z-o'zidan tiklanadi. Farq bo'lmasa hech
     narsa yuborilmaydi, ya'ni tinch holatda bu arzon tekshiruv xolos.

  3. Faol oqimlarning bayt hisobi kuzatiladi — ikki tekshiruv orasida
     qo'zg'almagan tayyor oqim "muzlagan" deb belgilanadi (hodisa + alert).

Natijada qo'lda aralashish kerak emas: kamera qo'shildi/o'chirildi yoki
MediaMTX yiqildi — 30 soniya ichida tizim o'zini kerakli holatga keltiradi.
"""
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from core import bus, events
from core.db import get_db
from core.log import log

from . import sync

CHECK_INTERVAL = 30.0      # soniya — to'liq sinxronlash (yo'llar kelishtiriladi)
# Muzlash tekshiruvi ancha tez-tez: oqim qotganini 60 soniyada bilish
# kuzatuv tizimi uchun juda kech. Faol yo'llar ro'yxati kichik (yo'llar
# talab bo'yicha yaratiladi), shuning uchun bu arzon.
STALL_INTERVAL = 5.0       # soniya
SPAWN_COOLDOWN = 30.0      # qayta urinishlar orasidagi eng kam vaqt
STARTUP_WAIT = 8.0         # ishga tushirgandan keyin API'ni shuncha kutamiz

MEDIAMTX_EXE = sync.BASE_DIR / "mediamtx" / (
    "mediamtx.exe" if os.name == "nt" else "mediamtx")
LOG_PATH = sync.DATA_DIR / "mediamtx.log"

_started = False
_lock = threading.Lock()
_process: subprocess.Popen | None = None
_last_spawn = 0.0

_prev_bytes: dict[tuple[int, str], int] = {}   # (tugun, yo'l) -> bytesReceived
_stalled: dict[tuple[int, str], str] = {}      # (tugun, yo'l) -> ko'rsatma nomi

# Ortiqcha yo'llar. MediaMTX har `paths/add`/`delete` so'roviga butun
# konfiguratsiyani qayta yuklaydi, ya'ni bitta amal narxi mavjud yo'llar
# soniga chiziqli o'sadi (o'lchov: 0 yo'lda 9 ms, 2400 yo'lda 297 ms).
# Shu sababli tozalash vaqt byudjeti bilan chegaralangan va bir necha
# tsiklga cho'ziladi — shu davrda kamera ochilishi ham sekin bo'ladi.
#
# Eng tez yechim — MediaMTX'ni qayta ishga tushirish: API orqali
# qo'shilgan yo'llar faylga yozilmaydi (tekshirildi: 2051 -> 0), kerakli
# yo'l esa ko'rish so'ralganda o'zi qaytadan yaratiladi. Buni avtomatik
# qilmaymiz — tirik oqimlarni uzib yuborardi; operatorga aytamiz.
BLOAT_WARN_EVERY = 300.0                       # soniya
_pending: dict[int, int] = {}                  # tugun -> tozalanmagan yo'llar
_bloat_warned: dict[int, float] = {}

# Oxirgi to'liq sinxronda API'si javob bergan tugunlar. Tez tsikl faqat
# shularni tekshiradi — o'lik tugunning timeout'i tsiklni cho'zmasin.
_reachable: set[int] = set()


def stalled_paths() -> set[str]:
    """Ayni damda muzlagan (bayt kelmayotgan) faol yo'llar."""
    with _lock:
        return set(_stalled.values())


def stalled_count(node_id: int) -> int:
    """Bitta tugundagi muzlagan oqimlar soni — tugun salomatligi uchun."""
    with _lock:
        return sum(1 for key in _stalled if key[0] == node_id)


def pending_count(node_id: int) -> int:
    """Tugunda hali tozalanmagan ortiqcha yo'llar — 0 bo'lishi kerak."""
    with _lock:
        return _pending.get(node_id, 0)


def _note_pending(node: dict, pending: int) -> None:
    """Tozalanmagan yo'llarni qayd etadi va operatorni ogohlantiradi."""
    with _lock:
        _pending[node["id"]] = pending
    if not pending:
        return
    now = time.monotonic()
    with _lock:
        if now - _bloat_warned.get(node["id"], 0.0) < BLOAT_WARN_EVERY:
            return
        _bloat_warned[node["id"]] = now
    log("reconciler", "paths_bloated", level="warning", node=node["name"],
        pending=pending,
        message="MediaMTX'da ortiqcha yo'llar ko'p — tozalanmoqda, shu "
                "davrda kamera sekinroq ochiladi. Tezroq yo'l: MediaMTX'ni "
                "qayta ishga tushiring (yo'llar faylga yozilmaydi, "
                "kerakligi ko'rilganda o'zi tiklanadi).")


def _nodes() -> list[dict]:
    """Yoqilgan MediaMTX tugunlari; jadval bo'sh bo'lsa — lokal standart."""
    try:
        with get_db() as db:
            rows = db.execute("SELECT * FROM nodes WHERE enabled = 1").fetchall()
        nodes = [dict(r) for r in rows]
    except Exception:
        nodes = []
    return nodes or [{"id": 1, "name": "Asosiy", "api_base": sync.API_BASE}]


def _autostart_allowed() -> bool:
    if os.environ.get("MEDIAMTX_AUTOSTART", "1") == "0":
        return False
    return MEDIAMTX_EXE.exists()


def _spawn() -> bool:
    """Lokal MediaMTX'ni ishga tushiradi; log ildizdagi mediamtx.log da."""
    global _process, _last_spawn
    now = time.monotonic()
    if now - _last_spawn < SPAWN_COOLDOWN:
        return False
    if _process is not None and _process.poll() is None:
        return False               # biz ochgan jarayon tirik — hali ko'tarilyapti
    _last_spawn = now
    try:
        log_file = open(LOG_PATH, "ab")
        _process = subprocess.Popen(
            [str(MEDIAMTX_EXE), str(sync.CONFIG_PATH)],
            cwd=str(sync.BASE_DIR),
            stdout=log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        log("reconciler", "mediamtx_spawn_failed", level="error", error=str(exc))
        return False
    log("reconciler", "mediamtx_restarted", log_file=LOG_PATH.name)
    try:
        with get_db() as db:
            events.add(db, "mediamtx", detail="MediaMTX qayta ishga tushirildi")
    except Exception:
        pass
    # API ko'tarilishini qisqa kutamiz — yo'llar shu tickning o'zida tiklansin.
    deadline = time.monotonic() + STARTUP_WAIT
    while time.monotonic() < deadline:
        if sync.api_available():
            return True
        time.sleep(0.5)
    return False


def _check_stalls(node: dict) -> None:
    """Faol oqimlarning bayt hisobi ikki tick orasida qo'zg'almasa — muzlagan.

    TCP tekshiruv (health) buni ko'rmaydi: registrator portga javob
    beraveradi, lekin kanal tasvir bermay qolishi mumkin. bytesReceived
    esa yolg'on gapirmaydi — 30 soniyada bitta bayt ham kelmagan tayyor
    oqim aniq muzlagan.
    """
    node_id = node["id"]
    active = sync.list_active_paths(node["api_base"])
    if active is None:
        return
    changes: list[tuple[str, str, str]] = []     # (ko'rsatma, yo'l, holat)
    with _lock:
        for name, item in active.items():
            key = (node_id, name)
            # Tomoshabini bor sub yo'l issiq bo'lib turaversin. Issiqlik
            # 10 daqiqada so'nadi va o'shanda sourceOnDemand qayta
            # yoqiladi — ya'ni ko'rib o'tirgan odamning oqimi uziladi.
            # Ko'rish davom etayotganini aynan shu ro'yxat bilamiz.
            if item.get("readers") and name.endswith(sync.SUB_SUFFIX):
                sync.mark_warm(name)
            if not item.get("ready"):
                continue                          # hali ulanmagan — muzlash emas
            got = int(item.get("bytesReceived") or 0)
            prev = _prev_bytes.get(key)
            if prev is not None and got == prev:
                if key not in _stalled:
                    display = name if node_id == 1 else f"{name}@{node['name']}"
                    _stalled[key] = display
                    changes.append((display, name, "stalled"))
            elif key in _stalled:
                changes.append((_stalled.pop(key), name, "resumed"))
        for key in list(_stalled):
            if key[0] == node_id and key[1] not in active:
                _stalled.pop(key)                 # oqim yopildi — muzlash tugadi
        for key in [k for k in _prev_bytes if k[0] == node_id]:
            _prev_bytes.pop(key)
        _prev_bytes.update({(node_id, n): int(i.get("bytesReceived") or 0)
                            for n, i in active.items()})
    if not changes:
        return
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_db() as db:
        for display, name, kind in changes:
            events.add(db, kind, slug=display,
                       detail="oqim muzladi" if kind == "stalled" else "oqim tiklandi")
            log("reconciler", f"stream_{kind}",
                level="warning" if kind == "stalled" else "info", path=display)

            # SSE: yo'l nomidan kamera topiladi (suffikslar olib tashlanadi).
            # `resumed` tashqariga `online` bo'lib chiqadi — abonent uchun
            # holat lug'ati bitta: online/offline/stalled.
            base = name
            for suffix in (sync.TRANSCODE_SUFFIX, sync.SUB_SUFFIX):
                if base.endswith(suffix):
                    base = base[: -len(suffix)]
            row = db.execute("SELECT id, external_id FROM cameras WHERE slug = ?",
                             (base,)).fetchone()
            if row:
                bus.publish("state", {
                    "id": row["id"],
                    "external_id": row["external_id"] or "",
                    "state": "stalled" if kind == "stalled" else "online",
                    "at": at,
                })


def _tick(load_cameras: Callable[[], list[dict]], announce: bool) -> bool:
    """Bitta tekshiruv (barcha tugunlar). Sinxron bajarilsa True qaytaradi."""
    cameras = load_cameras()
    synced = False
    for node in _nodes():
        api = node["api_base"]
        local = sync.is_local_api(api)
        if not sync.api_available(api):
            if not (local and _autostart_allowed() and _spawn()):
                with _lock:
                    _reachable.discard(node["id"])
                continue
        with _lock:
            _reachable.add(node["id"])
        node_cams = [c for c in cameras
                     if (c.get("node_id") or 1) == node["id"]]
        result = sync.push_to_api(node_cams, api_base=api)
        _note_pending(node, result.get("pending", 0))
        changed = result["added"] + result["updated"] + result["removed"]
        if announce or changed or not result["ok"]:
            log("reconciler", "sync",
                level="info" if result["ok"] else "warning",
                node=node["name"], added=result["added"],
                updated=result["updated"], removed=result["removed"],
                message=result["message"])
        synced = synced or result["ok"]
    return synced


def _watch_active() -> None:
    """Faqat muzlash tekshiruvi — to'liq sinxronsiz, tez tsikl uchun.

    Faol yo'llar ro'yxati kichik (yo'llar talab bo'yicha yaratilgani
    uchun MediaMTX'da faqat ko'rilayotganlari turadi), shuning uchun buni
    5000 kamerada ham 5 soniyada bir chaqirish arzon.

    Javob bermayotgan tugun o'tkazib yuboriladi: aks holda har tsikl
    uning timeout'ini (4 s) kutib o'tirardi. Uni to'liq sinxron
    (`_tick`) qayta sinaydi.
    """
    with _lock:
        alive = set(_reachable)
    for node in _nodes():
        if node["id"] in alive:
            _check_stalls(node)


PRUNE_INTERVAL = 3600.0    # soniya — eski hodisalar soatiga bir tozalanadi


def _loop(load_cameras: Callable[[], list[dict]]) -> None:
    announced = False              # birinchi muvaffaqiyatli sinxron logda ko'rinsin
    last_prune = 0.0
    last_sync = 0.0
    while True:
        try:
            now = time.monotonic()
            if not last_sync or now - last_sync >= CHECK_INTERVAL:
                last_sync = now
                if _tick(load_cameras, not announced):
                    announced = True
                if now - last_prune > PRUNE_INTERVAL:
                    last_prune = now
                    with get_db() as db:
                        events.prune(db)
            # Muzlash tekshiruvi har tsiklda — to'liq sinxrondan ancha
            # tez-tez. Tomoshabin bor oqim qotganini 60 soniyada emas,
            # 5-10 soniyada bilamiz.
            _watch_active()
        except Exception as exc:   # kuzatuv hech qachon yiqilmasin
            log("reconciler", "tick_failed", level="error", error=str(exc))
        time.sleep(STALL_INTERVAL)


def start(load_cameras: Callable[[], list[dict]]) -> None:
    """Fon reconcilerini ishga tushiradi (bir marta).

    `load_cameras` — kameralarning MediaMTX ko'rinishini qaytaruvchi
    funksiya; uni app qatlami uzatadi (media qatlami bazaga sxema
    darajasida bog'lanmasin).
    """
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, args=(load_cameras,), daemon=True).start()

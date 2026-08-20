"""Nigoh — kamera suratlarini diskda saqlash va pog'onali yangilash.

Suratlar bazada emas, diskda turadi (`{DATA_DIR}/snapshots/{slug}.jpg`) —
SQLite 5000 ta 200 KB blob bilan portlaydi, fayl tizimi esa aynan shu ish
uchun qilingan. Bazada faqat `snapshot_at` (oxirgi surat vaqti).

Yangilash pog'onalari (fon vazifasi, parallellik 8):

  * issiq — oxirgi 5 daqiqada so'ralgan kamera: har 10 soniyada;
  * sovuq — qolganlar: kamera soniga moslashadigan oraliqda (kamida
    10 daqiqa — SNAP_INTERVAL bilan o'zgartiriladi, 5000 kamerada
    ~17 daqiqa), faqat tirik (online) bo'lsa;
  * o'chiq (disabled) yoki o'chib qolgan (offline): umuman yangilanmaydi.

Jadval FAZA bilan tarqatilgan: har kamera id'sidan hisoblangan barqaror
siljish oladi va absolyut vaqt oynasida o'z uyasida bir marta olinadi.
Shu tufayli:

  * birinchi ishga tushishda 5000 surat birdan so'ralmaydi;
  * restartdan keyin diskdagi yangi fayllar qayta olinmaydi (fayl yoshi
    oynaga sig'sa — bajarilgan hisoblanadi);
  * birga navbatga tushgan guruhlar shakllanmaydi — oyna "oxirgi
    yangilanish"ga emas, taqvimga bog'langan.

Urinish muvaffaqiyatsiz bo'lsa ham oyna bajarilgan deb belgilanadi —
buzuq kamera har tickda qayta urinilib registratorni bo'g'masin;
keyingi oynada o'zi qayta uriniladi.

Har muvaffaqiyatli yangilanish SSE'ga `snapshot` hodisasi bo'lib chiqadi.
"""
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

from . import bus, fast_start, health, security
from .db import DATA_DIR, get_db
from .log import log

SNAP_DIR = DATA_DIR / "snapshots"

TICK = 10.0             # fon tsikli qadami (issiq interval bilan bir xil)
HOT_WINDOW = 300.0      # so'ralganidan keyin shuncha vaqt "issiq"
HOT_INTERVAL = 10.0
# Sovuq oraliq: hech kim so'ramayotgan kamera surati shu oraliqda bir
# yangilanadi. Surat "jonli" bo'lishi shart emas — standart 10 daqiqa,
# kerak bo'lsa SNAP_INTERVAL muhit o'zgaruvchisi bilan o'zgartiriladi.
COLD_MIN_INTERVAL = float(os.environ.get("SNAP_INTERVAL", "600"))
# Sovuq oraliq kamera soniga moslashadi: umumiy tezlik ~5 surat/soniyada
# ushlanadi. Registratorlar bo'g'ilsa 0,5 ga ko'taring.
COLD_PER_CAMERA = 0.2
# Qadam boshiga zaxira klapan — faza to'g'ri ishlasa tegilmaydi (5000
# kamerada cho'qqi ~71). Logda muntazam ko'rinsa oraliq noto'g'ri.
BATCH_LIMIT = 96
WORKERS = 8
# read() dagi jonli olish slotlari — 64 katakli devor birinchi ochilganda
# 64 FFmpeg API threadpool'ini yeb qo'ymasin. Bu himoya, tegmang.
LIVE_SLOTS = 2

# Yetim fayllar: kamera o'chirilganda {slug}.jpg qolib ketadi — kuniga
# bir tekshiriladi, bazada yo'q va bir haftadan eski fayl o'chiriladi.
# Bir hafta saqlash ataylab: o'chirish tasodifiy bo'lsa oxirgi kadr
# diagnostika uchun turadi.
CLEAN_EVERY = 86_400.0
ORPHAN_KEEP = 7 * 86_400.0

_requested: dict[int, float] = {}   # camera_id -> oxirgi so'ralgan (monotonic)
_done: dict[int, float] = {}        # camera_id -> oxirgi urinish (epoch)
_last_total = 0                     # oxirgi tsikldagi kameralar soni
_last_clean = 0.0
_cycle = {"total": 0, "ok": 0, "duration_ms": 0, "at": ""}
_lock = threading.Lock()
_started = False
_live_sem = threading.BoundedSemaphore(LIVE_SLOTS)


def path_for(slug: str) -> Path:
    return SNAP_DIR / f"{slug}.jpg"


def cold_interval(total: int) -> float:
    return max(COLD_MIN_INTERVAL, total * COLD_PER_CAMERA)


def cycle_stats() -> dict:
    """Oxirgi tsikl haqida — /health va konsol 'Fon vazifalari' uchun."""
    with _lock:
        return dict(_cycle)


def max_age() -> float:
    """Suratning "hali yaroqli" yoshi — sovuq oraliqning 3 baravari.

    Yumshoq chegara: asosiy ishni holat tekshiruvi qiladi, bu faqat
    "holat yolg'on gapiryapti" holatini ushlaydi. Issiq oraliqqa
    bog'lanmaydi — aks holda barcha sovuq kameralar "eskirgan" chiqadi.
    """
    with _lock:
        total = _last_total
    return 3 * cold_interval(total)


def _offset(camera_id: int, interval: float) -> float:
    """Kameraning oraliq ichidagi barqaror siljishi — har restartda bir xil.

    Ketma-ket id'lar ham tekis tarqalsin deb oltin nisbat ko'paytmasi
    ishlatiladi.
    """
    return (camera_id * 2654435761) % max(1, int(interval))


def _slot(camera_id: int, interval: float, now: float) -> float:
    """Kameraning joriy (allaqachon kelgan) uyasi — absolyut vaqtda."""
    off = _offset(camera_id, interval)
    return ((now - off) // interval) * interval + off


def note_request(camera_id: int) -> None:
    """Surat so'raldi — kamera 5 daqiqaga issiq pog'onaga o'tadi."""
    with _lock:
        _requested[camera_id] = time.monotonic()
        if len(_requested) > 20_000:            # xotira chegarasi
            cutoff = time.monotonic() - HOT_WINDOW
            for key in [k for k, t in _requested.items() if t < cutoff]:
                _requested.pop(key, None)


def read(row, live: bool = True) -> tuple[bytes | None, str, float]:
    """Kameraning suratini beradi: avval disk, bo'lmasa jonli olish.

    Qaytaradi (bytes|None, etag, fayl_vaqti_epoch). So'rov issiqlik
    hisobiga yoziladi — keyingi yangilanishlar fon vazifasida boradi.

    Jonli olish semafor bilan chegaralangan (LIVE_SLOTS): slot bo'sh
    bo'lmasa darhol bo'sh qaytadi — 64 katak birdan ochilganda API
    muzlab qolmaydi, fon tsikli bir necha soniyada to'ldiradi.
    `live=False` — faqat disk (offline/stale holatlari uchun).
    """
    note_request(row["id"])
    p = path_for(row["slug"] or "")

    def _from_disk():
        stat = p.stat()
        return (p.read_bytes(),
                f'"{int(stat.st_mtime)}-{stat.st_size}"', stat.st_mtime)

    try:
        return _from_disk()
    except OSError:
        pass
    if live and _live_sem.acquire(blocking=False):
        try:
            captured = capture(row)
        finally:
            _live_sem.release()
        if captured:
            try:
                return _from_disk()
            except OSError:
                pass
    return None, "", 0.0


def capture(row) -> bool:
    """Bitta kameradan surat olib diskka yozadi, bazaga vaqtni belgilaydi."""
    data = fast_start.snapshot(
        row["id"], row["ip"], row["username"] or "",
        security.decrypt(row["password_enc"]),
        row["vendor"] or "", row["rtsp_path"] or "", row["slug"] or "")
    if not data:
        return False
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        SNAP_DIR.mkdir(parents=True, exist_ok=True)
        path_for(row["slug"] or "").write_bytes(data)
    except OSError as exc:
        log("snapshots", "write_failed", level="error", error=str(exc))
        return False
    with _lock:
        _done[row["id"]] = time.time()
    with get_db() as db:
        db.execute("UPDATE cameras SET snapshot_at = ? WHERE id = ?",
                   (at, row["id"]))
    bus.publish("snapshot", {"id": row["id"],
                             "external_id": row["external_id"] or "",
                             "at": at})
    return True


def _due_cameras() -> list:
    """Shu tickda olinadigan kameralar — har biri o'z uyasida, bir marta.

    Issiqlar ro'yxat boshida: klapan (BATCH_LIMIT) ishga tushsa devorda
    ko'rilayotgan kadrlar qurbon bo'lmaydi.
    """
    global _last_total
    now = time.time()
    mono = time.monotonic()
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM cameras WHERE enabled = 1 "
            "AND ip IS NOT NULL AND ip != ''"
        ).fetchall()
    interval_cold = cold_interval(len(rows))
    with _lock:
        _last_total = len(rows)
        requested = dict(_requested)
        done = dict(_done)

    hot_due, cold_due = [], []
    for row in rows:
        alive = health.online(row["ip"], row["port"])
        if alive is False:
            continue                            # offline — urinish behuda
        hot = mono - requested.get(row["id"], -1e12) < HOT_WINDOW
        if not hot and alive is not True:
            continue                            # sovuq faqat aniq online
        interval = HOT_INTERVAL if hot else interval_cold
        slot = _slot(row["id"], interval, now)
        prev = done.get(row["id"])
        if prev is None:
            # Restart: diskdagi fayl shu uyadan yangi bo'lsa — bajarilgan.
            # Busiz har restart 5000 ta behuda surat degani.
            try:
                mtime = path_for(row["slug"] or "").stat().st_mtime
            except OSError:
                mtime = 0.0
            if mtime >= slot:
                with _lock:
                    _done[row["id"]] = mtime
                continue
        elif prev >= slot:
            continue                            # bu oyna allaqachon bajarilgan
        (hot_due if hot else cold_due).append(row)

    due = hot_due + cold_due
    if len(due) > BATCH_LIMIT:
        log("snapshots", "batch_capped", level="warning",
            due=len(due), limit=BATCH_LIMIT)
        due = due[:BATCH_LIMIT]
    return due


def _attempt(row) -> bool:
    """Bitta urinish; natijadan qat'i nazar oyna bajarilgan deb belgilanadi."""
    try:
        return capture(row)
    finally:
        with _lock:
            _done[row["id"]] = time.time()


def _clean_orphans() -> None:
    """Bazada yo'q slug'larning eski suratlarini o'chiradi."""
    with get_db() as db:
        slugs = {r["slug"] for r in db.execute("SELECT slug FROM cameras")}
    try:
        files = list(SNAP_DIR.glob("*.jpg"))
    except OSError:
        return
    now, removed = time.time(), 0
    for f in files:
        if f.stem in slugs:
            continue
        try:
            if now - f.stat().st_mtime > ORPHAN_KEEP:
                f.unlink()
                removed += 1
        except OSError:
            pass
    if removed:
        log("snapshots", "orphans_cleaned", removed=removed)


def _loop() -> None:
    global _last_clean
    while True:
        try:
            if time.time() - _last_clean > CLEAN_EVERY:
                _last_clean = time.time()
                _clean_orphans()
            due = _due_cameras()
            if due:
                started = time.monotonic()
                with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                    results = list(pool.map(_attempt, due))
                stats = {"total": len(due),
                         "ok": sum(1 for r in results if r),
                         "duration_ms": int((time.monotonic() - started) * 1000),
                         "at": datetime.now(timezone.utc).isoformat(
                             timespec="seconds")}
                with _lock:
                    _cycle.update(stats)
                log("snapshots", "cycle", **stats)
        except Exception as exc:                # fon vazifa yiqilmasin
            log("snapshots", "cycle_failed", level="error", error=str(exc))
        time.sleep(TICK)


def start() -> None:
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, daemon=True).start()

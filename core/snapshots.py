"""Nigoh — kamera suratlarini diskda saqlash va pog'onali yangilash.

Suratlar bazada emas, diskda turadi (`{DATA_DIR}/snapshots/{slug}.jpg`) —
SQLite 5000 ta 200 KB blob bilan portlaydi, fayl tizimi esa aynan shu ish
uchun qilingan. Bazada faqat `snapshot_at` (oxirgi surat vaqti).

Yangilash pog'onalari (fon vazifasi, parallellik 8):

  * issiq — oxirgi 5 daqiqada so'ralgan kamera: har 10 soniyada;
  * sovuq — qolganlar: har 5 daqiqada, faqat tirik (online) bo'lsa;
  * o'chiq (disabled) yoki o'chib qolgan (offline): umuman yangilanmaydi.

Har muvaffaqiyatli yangilanish SSE'ga `snapshot` hodisasi bo'lib chiqadi.
"""
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from concurrent.futures import ThreadPoolExecutor

from . import bus, fast_start, health, security
from .db import DATA_DIR, get_db
from .log import log

SNAP_DIR = DATA_DIR / "snapshots"

TICK = 10.0             # fon tsikli qadami (issiq interval bilan bir xil)
HOT_WINDOW = 300.0      # so'ralganidan keyin shuncha vaqt "issiq"
HOT_INTERVAL = 10.0
COLD_INTERVAL = 300.0
WORKERS = 8

_requested: dict[int, float] = {}   # camera_id -> oxirgi so'ralgan vaqt
_updated: dict[int, float] = {}     # camera_id -> oxirgi yangilangan vaqt
_lock = threading.Lock()
_started = False


def path_for(slug: str) -> Path:
    return SNAP_DIR / f"{slug}.jpg"


def note_request(camera_id: int) -> None:
    """Surat so'raldi — kamera 5 daqiqaga issiq pog'onaga o'tadi."""
    with _lock:
        _requested[camera_id] = time.monotonic()
        if len(_requested) > 20_000:            # xotira chegarasi
            cutoff = time.monotonic() - HOT_WINDOW
            for key in [k for k, t in _requested.items() if t < cutoff]:
                _requested.pop(key, None)


def read(row) -> tuple[bytes | None, str]:
    """Kameraning suratini beradi: avval disk, bo'lmasa jonli olish.

    Qaytaradi (bytes|None, etag). So'rov issiqlik hisobiga yoziladi —
    keyingi yangilanishlar fon vazifasida har 10 soniyada boradi.
    """
    note_request(row["id"])
    p = path_for(row["slug"] or "")
    try:
        stat = p.stat()
        return p.read_bytes(), f'"{int(stat.st_mtime)}-{stat.st_size}"'
    except OSError:
        pass
    # Diskda hali yo'q — birinchi so'rov jonli oladi (va diskka yozadi).
    if capture(row):
        try:
            stat = p.stat()
            return p.read_bytes(), f'"{int(stat.st_mtime)}-{stat.st_size}"'
        except OSError:
            pass
    return None, ""


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
        _updated[row["id"]] = time.monotonic()
    with get_db() as db:
        db.execute("UPDATE cameras SET snapshot_at = ? WHERE id = ?",
                   (at, row["id"]))
    bus.publish("snapshot", {"id": row["id"],
                             "external_id": row["external_id"] or "",
                             "at": at})
    return True


def _due_cameras() -> list:
    now = time.monotonic()
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM cameras WHERE enabled = 1 "
            "AND ip IS NOT NULL AND ip != ''"
        ).fetchall()
    due = []
    with _lock:
        requested = dict(_requested)
        updated = dict(_updated)
    for row in rows:
        alive = health.online(row["ip"], row["port"])
        if alive is False:
            continue                            # offline — urinish behuda
        hot = now - requested.get(row["id"], -1e9) < HOT_WINDOW
        interval = HOT_INTERVAL if hot else COLD_INTERVAL
        if not hot and alive is not True:
            continue                            # sovuq faqat aniq online
        if now - updated.get(row["id"], -1e9) >= interval:
            due.append(row)
    return due


def _loop() -> None:
    while True:
        try:
            due = _due_cameras()
            if due:
                started = time.monotonic()
                with ThreadPoolExecutor(max_workers=WORKERS) as pool:
                    results = list(pool.map(capture, due))
                log("snapshots", "cycle", total=len(due),
                    ok=sum(1 for r in results if r),
                    duration_ms=int((time.monotonic() - started) * 1000))
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

"""Nigoh — ma'lumotlar bazasi: ulanish, sxema va migratsiya."""
import os
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path

# Loyiha ildizi — bu fayl core/ ichida turadi.
BASE_DIR = Path(__file__).resolve().parent.parent

# Ma'lumot fayllari (baza, kalit, loglar, mediamtx.yml) qayerda saqlanadi.
# Standart — loyiha ildizi (Windows'da hozirgidek). Konteynerda NIGOH_DATA
# orqali alohida volume beriladi: kod almashsa ham ma'lumot joyida qoladi.
DATA_DIR = Path(os.environ.get("NIGOH_DATA") or BASE_DIR)
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DATA_DIR / "cameras.db"

# Ilgari yangi baza demo kameralar bilan to'ldirilardi — endi seed yo'q
# (server bo'sh boshlanadi). Konstanta 2-migratsiya uchun qoldi: mavjud
# bazalardagi demo yozuvlarni shu manzil bo'yicha topib o'chiradi.
DEMO_STREAM = "https://test-streams.mux.dev/x36xhzz/x36xhzz.m3u8"

# Eski bazani yangi ustunlar bilan to'ldirish uchun (ALTER TABLE).
CAMERA_EXTRA_COLUMNS = {
    "slug": "TEXT",
    "ip": "TEXT",
    "port": "INTEGER NOT NULL DEFAULT 554",
    "username": "TEXT",
    "password_enc": "TEXT",
    "rtsp_path": "TEXT",
    "sub_path": "TEXT",                           # past sifatli 2-oqim (video devor)
    "sub_codec": "TEXT",                          # sub oqim kodegi (odatda H264)
    "vendor": "TEXT",
    "enabled": "INTEGER NOT NULL DEFAULT 1",
    "note": "TEXT",
    "codec": "TEXT",                              # kameradan kelayotgan kodek
    "resolution": "TEXT",                         # SDP'dan: "1920x1080" yoki bo'sh
    "transcode": "INTEGER NOT NULL DEFAULT 0",    # H.264 ga o'girish kerakmi
    "always_on": "INTEGER NOT NULL DEFAULT 0",    # doim tayyor tursinmi
    "last_seen": "TEXT",                          # oxirgi marta onlayn bo'lgan vaqt (UTC)
    "node_id": "INTEGER NOT NULL DEFAULT 1",      # qaysi MediaMTX tuguni tortadi
    # Tashqi tizim identifikatori: asosiy tizim o'z ID'si bilan murojaat
    # qiladi (ext:...), mapping jadval yuritmaydi.
    "external_id": "TEXT",
    # Qurilma pasporti (/devices/info to'ldiradi): eski firmware'larni
    # topish va ta'minotchi bilan gaplashish uchun.
    "model": "TEXT",
    "firmware": "TEXT",
    # Oxirgi muvaffaqiyatli surat vaqti — suratning o'zi diskda
    # ({DATA_DIR}/snapshots/{slug}.jpg), bazada blob saqlanmaydi.
    "snapshot_at": "TEXT",
}

# Kamera ko'payganda xaritani va ro'yxatni tez ushlab turadigan indekslar.
INDEXES = [
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_cameras_slug ON cameras(slug)",
    "CREATE INDEX IF NOT EXISTS idx_cameras_region ON cameras(region)",
    "CREATE INDEX IF NOT EXISTS idx_cameras_enabled ON cameras(enabled)",
    "CREATE INDEX IF NOT EXISTS idx_cameras_bbox ON cameras(lat, lng)",
    "CREATE INDEX IF NOT EXISTS idx_sessions_expires ON sessions(expires_at)",
    "CREATE INDEX IF NOT EXISTS idx_events_ts ON events(ts)",
    "CREATE INDEX IF NOT EXISTS idx_cameras_node ON cameras(node_id)",
    # Bo'sh/NULL qiymatlar cheklovga tushmaydi — external_id ixtiyoriy.
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_cameras_external "
    "ON cameras(external_id) WHERE external_id IS NOT NULL AND external_id != ''",
]


@contextmanager
def get_db():
    # WAL rejimida yozish o'qishlarni qulflamaydi — fon kuzatuvi (health)
    # har daqiqa yozayotganda ham so'rovlar "database is locked" olmaydi.
    # timeout — baribir to'qnashilsa, xato o'rniga kutib beradi.
    db = sqlite3.connect(DB_PATH, timeout=10)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA synchronous=NORMAL")
    try:
        yield db
        db.commit()
    finally:
        db.close()


# ---------- slug ----------

# O'zbek lotin yozuvidagi maxsus belgilar va kirill harflari.
_TRANSLIT = {
    "ʻ": "", "ʼ": "", "'": "", "`": "", "‘": "", "’": "",
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "yo",
    "ж": "j", "з": "z", "и": "i", "й": "y", "к": "k", "л": "l", "м": "m",
    "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t", "у": "u",
    "ф": "f", "х": "x", "ц": "s", "ч": "ch", "ш": "sh", "щ": "sh", "ъ": "",
    "ы": "i", "ь": "", "э": "e", "ю": "yu", "я": "ya", "ў": "o", "қ": "q",
    "ғ": "g", "ҳ": "h",
}


def slugify(text: str) -> str:
    """MediaMTX path nomi uchun xavfsiz kalit: faqat a-z, 0-9 va _."""
    s = "".join(_TRANSLIT.get(ch, ch) for ch in text.lower())
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "kamera"


def unique_slug(db, base: str, exclude_id: int | None = None) -> str:
    """Bazada takrorlanmaydigan slug qaytaradi: nom, nom_2, nom_3 …"""
    base = slugify(base)
    candidate, n = base, 1
    while True:
        sql = "SELECT id FROM cameras WHERE slug = ?"
        params: list = [candidate]
        if exclude_id is not None:
            sql += " AND id != ?"
            params.append(exclude_id)
        if db.execute(sql, params).fetchone() is None:
            return candidate
        n += 1
        candidate = f"{base}_{n}"


# ---------- sxema ----------

def init_db() -> None:
    with get_db() as db:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS cameras (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                region TEXT NOT NULL,
                lat REAL NOT NULL,
                lng REAL NOT NULL,
                stream_url TEXT NOT NULL
            )
            """
        )
        _migrate_cameras(db)

        # Foydalanuvchilar. Tarixiy sabab bilan jadval nomi `admins` — endi
        # rollar bor: 'admin' hammasini boshqaradi, 'operator' esa faqat
        # o'ziga biriktirilgan hududlardagi kameralarni ko'radi.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS admins (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                username TEXT NOT NULL UNIQUE,
                pw_hash TEXT NOT NULL,
                pw_salt TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        existing = {row["name"] for row in db.execute("PRAGMA table_info(admins)")}
        if "role" not in existing:
            db.execute("ALTER TABLE admins ADD COLUMN role TEXT NOT NULL "
                       "DEFAULT 'admin'")

        db.execute(
            """
            CREATE TABLE IF NOT EXISTS sessions (
                token TEXT PRIMARY KEY,
                admin_id INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                expires_at TEXT NOT NULL
            )
            """
        )
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                ts TEXT NOT NULL DEFAULT (datetime('now')),
                kind TEXT NOT NULL,   -- offline|online|stalled|resumed|mediamtx
                ip TEXT,
                port INTEGER,
                slug TEXT,
                detail TEXT
            )
            """
        )

        # MediaMTX tugunlari. Kameralar bir necha manzilda bo'lsa, har bir
        # joyga alohida MediaMTX qo'yiladi — kamera trafigi lokal tarmoqda
        # qoladi, magistralga faqat ko'rilayotgan oqim chiqadi. 1-tugun —
        # backend bilan bitta mashinadagi "asosiy" MediaMTX.
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS nodes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                api_base TEXT NOT NULL,               -- http://host:9997
                public_host TEXT NOT NULL DEFAULT '', -- bo'sh = so'rov hostidan
                rtsp_port INTEGER NOT NULL DEFAULT 8554,
                hls_port INTEGER NOT NULL DEFAULT 8888,
                webrtc_port INTEGER NOT NULL DEFAULT 8889,
                enabled INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        if db.execute("SELECT COUNT(*) FROM nodes").fetchone()[0] == 0:
            db.execute(
                "INSERT INTO nodes (id, name, api_base, public_host, rtsp_port, "
                "hls_port, webrtc_port) VALUES (1, 'Asosiy', ?, '', ?, ?, ?)",
                (os.environ.get("MEDIAMTX_API", "http://127.0.0.1:9997"),
                 int(os.environ.get("MEDIAMTX_RTSP_PORT", "8554")),
                 int(os.environ.get("HLS_PORT", "8888")),
                 int(os.environ.get("WEBRTC_PORT", "8889"))),
            )

        _run_migrations(db)

        for statement in INDEXES:
            db.execute(statement)


# ---------- raqamlangan migratsiyalar ----------
#
# CAMERA_EXTRA_COLUMNS tsikli "yetishmagan ustunni qo'shish"ni bajaradi —
# u idempotent va tartibga muhtoj emas. Undan tashqaridagi har qanday
# sxema o'zgarishi (jadval o'chirish, ma'lumot ko'chirish, indeksni
# almashtirish) shu ro'yxatga raqam bilan qo'shiladi: har biri bir marta,
# tartib bilan bajariladi, bajarilgani `schema_version` jadvalida turadi.


def _m1_kesish(db) -> None:
    """Mikroservisga o'tish: statistika, Telegram va operator hududlari
    asosiy tizimga ko'chdi — jadvallari olib tashlanadi (reja 3.1)."""
    for legacy in ("stats_region", "stats_event", "user_regions"):
        db.execute(f"DROP TABLE IF EXISTS {legacy}")


def _m2_demo_tozalash(db) -> None:
    """Eski seed'dan qolgan demo kameralarni o'chiradi — ishlab
    chiqarish bazasi bo'sh boshlanishi kerak. Faqat aynan demo oqim
    manziliga qaragan, IP'siz yozuvlar ketadi — haqiqiylarga tegilmaydi."""
    db.execute(
        "DELETE FROM cameras WHERE stream_url = ? AND (ip IS NULL OR ip = '')",
        (DEMO_STREAM,),
    )


MIGRATIONS = [
    (1, _m1_kesish),
    (2, _m2_demo_tozalash),
]


def schema_version(db) -> int:
    db.execute("CREATE TABLE IF NOT EXISTS schema_version "
               "(version INTEGER NOT NULL, applied_at TEXT NOT NULL "
               "DEFAULT (datetime('now')))")
    row = db.execute("SELECT MAX(version) FROM schema_version").fetchone()
    return row[0] or 0


def _run_migrations(db) -> None:
    current = schema_version(db)
    for version, migrate in MIGRATIONS:
        if version <= current:
            continue
        migrate(db)
        db.execute("INSERT INTO schema_version (version) VALUES (?)",
                   (version,))


def _migrate_cameras(db) -> None:
    """Eski `cameras` jadvaliga yetishmayotgan ustunlarni qo'shadi."""
    existing = {row["name"] for row in db.execute("PRAGMA table_info(cameras)")}
    for column, ddl in CAMERA_EXTRA_COLUMNS.items():
        if column not in existing:
            db.execute(f"ALTER TABLE cameras ADD COLUMN {column} {ddl}")
    db.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cameras_slug ON cameras(slug)")
    _backfill_slugs(db)


def _backfill_slugs(db) -> None:
    """Slug'i yo'q eski yozuvlarga nom asosida slug beradi."""
    rows = db.execute(
        "SELECT id, name, region FROM cameras WHERE slug IS NULL OR slug = ''"
    ).fetchall()
    for row in rows:
        slug = unique_slug(db, f"{row['region']}_{row['name']}", exclude_id=row["id"])
        db.execute("UPDATE cameras SET slug = ? WHERE id = ?", (slug, row["id"]))

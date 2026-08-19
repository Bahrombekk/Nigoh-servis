"""Nigoh — media qatlamining hodisalar jurnali (events jadvali).

Oqim muzladi/tiklandi, MediaMTX qayta ishga tushdi — tarmoq tekshiruvi
(TCP) ko'rmaydigan hodisalar shu yerga yoziladi. Kamera uzildi/qaytdi
tarixini esa `stats_event` yuritadi (core/stats.py) — ikkovi bir-birini
takrorlamaydi.
"""
RETENTION_DAYS = 30


def add(db, kind: str, *, ip: str | None = None, port: int | None = None,
        slug: str | None = None, detail: str = "") -> None:
    """Bitta hodisa yozadi. kind: stalled | resumed | mediamtx."""
    db.execute(
        "INSERT INTO events (kind, ip, port, slug, detail) VALUES (?, ?, ?, ?, ?)",
        (kind, ip, port, slug, detail),
    )


def prune(db) -> None:
    """Eski hodisalarni o'chiradi — jurnal cheksiz o'smasin."""
    db.execute("DELETE FROM events WHERE ts < datetime('now', ?)",
               (f"-{RETENTION_DAYS} days",))

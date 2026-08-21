"""Nigoh — media qatlamining hodisalar jurnali (events jadvali).

Bu jadval ikki xil hodisani yuritadi:

  * kamera uzildi/qaytdi (`online` / `offline`) — core/health.py yozadi;
  * oqim muzladi/tiklandi (`stalled` / `resumed`) va MediaMTX qayta
    ishga tushdi (`mediamtx`) — media/reconciler.py yozadi. Bularni
    TCP tekshiruvi ko'rmaydi: port ochiq bo'lsa ham tasvir kelmasligi
    mumkin.

Uzilishlar tahlili (uptime, MTTR, soatlik profil) aynan shu
yozuvlardan hisoblanadi — api/analytics.py ga qarang.
"""
RETENTION_DAYS = 30


def add(db, kind: str, *, ip: str | None = None, port: int | None = None,
        slug: str | None = None, detail: str = "") -> None:
    """Bitta hodisa yozadi.

    kind: online | offline (core/health.py) yoki
          stalled | resumed | mediamtx (media/reconciler.py).
    """
    db.execute(
        "INSERT INTO events (kind, ip, port, slug, detail) VALUES (?, ?, ?, ?, ?)",
        (kind, ip, port, slug, detail),
    )


def prune(db) -> None:
    """Eski hodisalarni o'chiradi — jurnal cheksiz o'smasin."""
    db.execute("DELETE FROM events WHERE ts < datetime('now', ?)",
               (f"-{RETENTION_DAYS} days",))

"""Nigoh — devor (mozaika) registri.

Qaysi kameralar, qaysi setkada birlashtiriladi — shu yerda saqlanadi.
Kalit (wall_key) tanlov + setkaning hashi: BIR XIL tanlovni bir necha
operator so'rasa, bitta kalit chiqadi va bitta mozaika oqimini bo'lishadi
(server bir marta kodlaydi).

Jadval `cameras.db` da (launcher ham, API ham shu bazani o'qiydi). MVP:
mozaika kamera SUB-oqimini to'g'ridan kameradan o'qiydi; kelgusida
MediaMTX relay orqali ulashiladi.
"""
from __future__ import annotations

import hashlib
import json

from core.db import get_db


def ensure_table() -> None:
    with get_db() as db:
        db.execute(
            """CREATE TABLE IF NOT EXISTS walls (
                key TEXT PRIMARY KEY,
                camera_ids TEXT NOT NULL,
                cols INTEGER NOT NULL,
                rows INTEGER NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )"""
        )


def wall_key(camera_ids: list[int], cols: int, rows: int) -> str:
    """Tanlov + setka -> qisqa kalit. Tartib muhim (katak joylashuvi),
    shuning uchun ID'lar tartibi saqlanadi."""
    raw = ",".join(str(i) for i in camera_ids) + f"|{cols}x{rows}"
    return hashlib.sha1(raw.encode()).hexdigest()[:12]


def save_wall(camera_ids: list[int], cols: int, rows: int) -> str:
    ensure_table()
    key = wall_key(camera_ids, cols, rows)
    with get_db() as db:
        db.execute(
            "INSERT OR REPLACE INTO walls (key, camera_ids, cols, rows, created_at) "
            "VALUES (?, ?, ?, ?, datetime('now'))",
            (key, json.dumps(camera_ids), cols, rows),
        )
    return key


def load_wall(key: str) -> dict | None:
    ensure_table()
    with get_db() as db:
        row = db.execute(
            "SELECT camera_ids, cols, rows FROM walls WHERE key = ?", (key,)
        ).fetchone()
    if not row:
        return None
    return {"camera_ids": json.loads(row["camera_ids"]),
            "cols": row["cols"], "rows": row["rows"]}


def wall_relays(key: str) -> dict | None:
    """Launcher uchun: har katak uchun LOKAL MediaMTX relay yo'l nomi + setka.

    Mozaika kameradan TO'G'RIDAN o'qimaydi — u MediaMTX orqa xonda ushlab
    turgan `<slug>_sub` (sub bo'lmasa `<slug>`) relay yo'lidan o'qiydi.
    Foydasi:
      • kamera bilan bitta ulanish (relay), necha devor bo'lsa ham;
      • uzilishni MediaMTX relay ushlaydi, mozaika lokaldan o'qiydi;
      • lokal ulanish — ochilish tez, tarmoq kutmaydi.

    Kodegi bo'sh (o'lik/xato parol/nostream) kamera -> None (qora katak):
    uning relay'i baribir ko'tarilmaydi va FFmpeg'ni yiqitardi.

    Qaytadi: {"relays": [<yo'l nomi|None>...], "cols", "rows"}. Relay
    tayyormi (ready) — buni launcher MediaMTX'dan tekshiradi va sovuq
    yoki ko'tarilmagan relayni qora katakka aylantiradi."""
    w = load_wall(key)
    if not w:
        return None
    ids = w["camera_ids"]
    rows = {}
    if ids:
        with get_db() as db:
            q = ",".join("?" * len(ids))
            for r in db.execute(
                f"SELECT id, slug, sub_path, codec, ip, enabled "
                f"FROM cameras WHERE id IN ({q})", ids
            ):
                rows[r["id"]] = r
    relays: list[str | None] = []
    for cid in ids:
        r = rows.get(cid)
        if not r or not r["enabled"] or not r["ip"] or not r["codec"]:
            relays.append(None)
            continue
        # Sub relay bo'lsa o'sha (yengil), aks holda asosiy relay.
        relays.append(r["slug"] + "_sub" if r["sub_path"] else r["slug"])
    return {"relays": relays, "cols": w["cols"], "rows": w["rows"]}

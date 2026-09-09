"""Nigoh — devor (mozaika) API.

Brauzer tanlagan kameralarni serverda BITTA katakli oqimga birlashtirish
uchun. `POST /walls` tanlovni saqlaydi va bitta mozaika oqimining manzilini
(+ katak xaritasi) qaytaradi. Brauzer 36 ta emas, bitta oqim ochadi.

Mozaikani FFmpeg yasaydi (`media/mosaic.py`), MediaMTX `~^wall_...$`
shabloni bo'yicha talab qilinganda ishga tushiradi (`stream_launcher.py`
-> `run_wall`). Bir xil tanlovni ko'pchilik so'rasa, bitta kalit chiqadi
va bitta oqimni bo'lishadi.
"""
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core import security
from core.db import get_db
from media import walls as walls_registry
from media.mosaic import grid_for

from .config import HLS_PORT, MEDIA_BASE, WEBRTC_PORT
from .helpers import media_host

router = APIRouter(prefix="/walls", tags=["walls"])


class WallIn(BaseModel):
    camera_ids: list[int] = Field(min_length=1, max_length=64)
    cols: int | None = Field(default=None, ge=1, le=8)
    rows: int | None = Field(default=None, ge=1, le=8)


@router.post("")
def create_wall(body: WallIn, request: Request):
    """Kamera tanlovidan mozaika oqimi yaratadi (yoki mavjudini qaytaradi).

    Qaytadi: `path` (wall_<kalit>), `stream_url`/`webrtc_url` (bitta oqim),
    `cols`/`rows` va `tiles` — har kamera qaysi katakda (bosib
    kattalashtirish uchun).
    """
    ids = list(dict.fromkeys(body.camera_ids))   # takrorlarni olib tashlaymiz, tartib saqlanadi
    with get_db() as db:
        q = ",".join("?" * len(ids))
        found = {r["id"]: r for r in db.execute(
            f"SELECT id, name, region, node_id, ip, codec FROM cameras "
            f"WHERE id IN ({q})", ids)}
    ids = [i for i in ids if i in found]
    if not ids:
        raise HTTPException(404, "Birorta kamera topilmadi")

    cols, rows = grid_for(len(ids), body.cols, body.rows)
    if cols * rows > 64:
        raise HTTPException(400, "Maksimal 64 katak (8×8)")

    # MVP: mozaika lokal tugunda quriladi (launcher faqat shu mashinada).
    nodes = {found[i]["node_id"] or 1 for i in ids}
    if nodes - {1}:
        raise HTTPException(400, "Hozircha faqat asosiy tugundagi kameralar "
                                 "bitta devorga birlashtiriladi")

    key = walls_registry.save_wall(ids, cols, rows)
    slug = "wall_" + key
    token = security.stream_token(slug)

    host = media_host(request)
    if MEDIA_BASE:
        stream_url = f"{MEDIA_BASE}/hls/{slug}/index.m3u8?token={token}"
        webrtc_url = f"{MEDIA_BASE}/whep/{slug}/whep?token={token}"
    else:
        stream_url = f"http://{host}:{HLS_PORT}/{slug}/index.m3u8?token={token}"
        webrtc_url = f"http://{host}:{WEBRTC_PORT}/{slug}/whep?token={token}"

    tiles = []
    for idx, cid in enumerate(ids):
        c = found[cid]
        col, row = idx % cols, idx // cols
        tiles.append({
            "camera_id": cid, "name": c["name"], "region": c["region"] or "",
            "col": col, "row": row,
            # Normallashtirilgan joylashuv — brauzer bosilgan nuqtani
            # katakka, katakni kameraga o'giradi (bosib kattalashtirish).
            "x": round(col / cols, 5), "y": round(row / rows, 5),
            "w": round(1 / cols, 5), "h": round(1 / rows, 5),
        })
    return {"path": slug, "mode": "direct", "cols": cols, "rows": rows,
            "stream_url": stream_url, "webrtc_url": webrtc_url, "tiles": tiles}

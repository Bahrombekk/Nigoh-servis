"""Nigoh — ochiq (kirishsiz) endpointlar: xarita ro'yxati, oqim, surat."""
from fastapi import APIRouter, HTTPException, Request, Response

from core import fast_start, health, security, snapshots
from core.db import get_db
from media import sync as mediamtx_sync

from .helpers import (camera_for_mediamtx, camera_state, node_info,
                      resolve_ref, stream_urls)

# Prefiks nisbiy — create_app uni /api/v1 (asosiy) va /api (eski) ostida ulaydi.
router = APIRouter(prefix="/cameras", tags=["cameras"])


@router.get("")
def list_cameras(request: Request, bbox: str = "", limit: int = 20000):
    """Xarita uchun kameralar — yengil ro'yxat.

    Oqim manzillari bu yerda yuborilmaydi: 1000 ta kamerada ular javobning
    yarmini egallaydi, holbuki bir vaqtda faqat bittasi ochiladi.
    Manzil `/api/v1/cameras/{id}/stream` dan olinadi.

    `bbox` berilsa (minLat,minLng,maxLat,maxLng) faqat shu to'rtburchak
    ichidagilar qaytariladi.
    """
    sql = ("SELECT id, external_id, name, region, lat, lng, ip, port, slug, "
           "enabled, last_seen, codec, sub_codec, resolution, transcode, "
           "always_on FROM cameras WHERE enabled = 1")
    params: list = []
    count_sql, count_params = sql.replace(
        "SELECT id, external_id, name, region, lat, lng, ip, port, slug, "
        "enabled, last_seen, codec, sub_codec, resolution, transcode, "
        "always_on ",
        "SELECT COUNT(*) "), list(params)
    if bbox:
        try:
            min_lat, min_lng, max_lat, max_lng = (float(v) for v in bbox.split(","))
        except ValueError:
            raise HTTPException(400, "bbox formati: minLat,minLng,maxLat,maxLng")
        sql += " AND lat BETWEEN ? AND ? AND lng BETWEEN ? AND ?"
        params += [min_lat, max_lat, min_lng, max_lng]
    sql += " ORDER BY region, name LIMIT ?"
    params.append(max(1, min(limit, 50000)))

    with get_db() as db:
        rows = db.execute(sql, params).fetchall()
        total = db.execute(count_sql, count_params).fetchone()[0]
    return {
        "total": total,
        "shown": len(rows),
        # IP tashqariga chiqmaydi — undan faqat tiriklik holati hisoblanadi.
        "cameras": [{
            "id": r["id"], "external_id": r["external_id"] or "",
            "name": r["name"], "region": r["region"],
            "lat": r["lat"], "lng": r["lng"],
            "online": health.online(r["ip"], r["port"]),
            # Yagona holat: disabled / unknown / offline / stalled / online.
            # `online` maydoni eski mijozlar uchun qoldirilgan.
            "state": camera_state(r),
            "last_seen": r["last_seen"] or "",
            "codec": r["codec"] or "",
            "sub_codec": r["sub_codec"] or "",
            "resolution": r["resolution"] or "",
            "transcode": bool(r["transcode"]),
            "always_on": bool(r["always_on"]),
        } for r in rows],
    }


@router.get("/status")
def cameras_status(request: Request, ids: str = "", all: int = 0):
    """Boshlang'ich holat — SSE (`/events`) ga ulanishdan oldin bir marta.

    `?ids=1,2,ext:cam-14` — tanlanganlar; `?all=1` — hammasi. Keyin faqat
    o'zgarishlarni SSE yetkazadi, poll qilish shart emas.

    """
    with get_db() as db:
        if all:
            rows = db.execute("SELECT * FROM cameras ORDER BY id").fetchall()
        elif ids.strip():
            refs = [p.strip() for p in ids.split(",") if p.strip()]
            if len(refs) > 1024:
                raise HTTPException(400, "Bitta so'rovda 1024 tagacha id")
            rows = [r for r in (resolve_ref(db, ref) for ref in refs)
                    if r is not None]
        else:
            raise HTTPException(400, "ids=1,2,... yoki all=1 bering")

    def out(r):
        keys = r.keys()
        return {
            "id": r["id"],
            "external_id": r["external_id"] or "",
            "state": camera_state(r),
            "codec": r["codec"] or "",
            "sub_codec": (r["sub_codec"] or "") if "sub_codec" in keys else "",
            "resolution": r["resolution"] or "",
            "last_seen": r["last_seen"] or "",
            "snapshot_at": (r["snapshot_at"] or "") if "snapshot_at" in keys else "",
        }

    return {"total": len(rows), "cameras": [out(r) for r in rows]}


@router.get("/{ref}/stream")
def camera_stream(ref: str, request: Request, hevc: int = 0,
                  quality: str = ""):
    """Bitta kameraning oqim manzili — ko'rish boshlanganda so'raladi.

    `ref` — ichki id (`123`) yoki tashqi id (`ext:cam-toshkent-014`).
    `hevc=1` — brauzer H.265 ni o'zi o'qiy oladi, o'girish kerak emas.
    `quality=sub` — past sifatli 2-oqim (video devor setkasi uchun);
    kamerada sub yo'l bo'lmasa asosiy oqim qaytadi.
    """
    with get_db() as db:
        row = resolve_ref(db, ref)
        if row is None or not row["enabled"]:
            raise HTTPException(404, "Kamera topilmadi")
        camera = camera_for_mediamtx(row)

    # Yo'l o'z tugunidagi MediaMTX'da borligiga ishonch hosil qilamiz —
    # u qayta ishga tushgan bo'lsa ham ko'rish shu yerda tiklanadi.
    if camera:
        node = node_info(camera["node_id"])
        api_base = node["api_base"] if node else None
        sub = mediamtx_sync.sub_variant(camera) if quality == "sub" else None
        if sub:
            # Issiq to'plam: keyingi 10 daqiqada qayta ochilish < 1 s.
            mediamtx_sync.mark_warm(sub["slug"])
            mediamtx_sync.ensure_path(sub, api_base)
        else:
            mediamtx_sync.ensure_path(camera, api_base)
            if api_base is None:               # o'girish faqat lokal tugunda
                mediamtx_sync.ensure_transcode_path(camera)
        # Kameradan darhol keyframe so'raymiz (ONVIF) — tasvir navbatdagi
        # keyframe'gacha (2-4 s) kutib qolmasin. Fonda ketadi, javobni
        # kechiktirmaydi; qo'llamaydigan kamera jim rad etadi. Sub yo'l
        # ko'rsatilayotganda so'rov ham sub oqimga ketadi.
        fast_start.request_keyframe_async(
            camera["ip"], camera["username"], camera["password"],
            camera["rtsp_path"], row["vendor"] or "",
            stream="sub" if sub else "main")
    return stream_urls(row, request, hevc_ok=bool(hevc), quality=quality)


@router.get("/{ref}/snapshot")
def camera_snapshot(ref: str, request: Request):
    """Kameraning JPEG surati — video ulangunicha darhol ko'rsatish uchun.

    `ref` — ichki id yoki `ext:...`. Player suratni poster sifatida
    qo'yadi: his qilinadigan ochilish ~100 ms bo'ladi, video esa orqa
    fonda ulanadi.
    """
    with get_db() as db:
        row = resolve_ref(db, ref)
    if row is None or not row["enabled"] or not row["ip"]:
        raise HTTPException(404, "Kamera topilmadi")

    # Surat disk zaxirasidan (core/snapshots yangilab turadi); birinchi
    # so'rovda jonli olinadi. ETag — brauzer/asosiy tizim o'zgarmagan
    # suratni qayta yuklamaydi (304).
    data, etag = snapshots.read(row)
    if not data:
        raise HTTPException(404, "Kameradan surat olib bo'lmadi")
    if etag and request.headers.get("if-none-match") == etag:
        return Response(status_code=304,
                        headers={"ETag": etag, "Cache-Control": "max-age=5"})
    headers = {"Cache-Control": "max-age=5"}
    if etag:
        headers["ETag"] = etag
    return Response(content=data, media_type="image/jpeg", headers=headers)

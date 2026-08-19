"""Nigoh — super-admin endpointlari: CRUD, NVR import, skaner, MediaMTX."""
import math
import sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import APIRouter, Depends, HTTPException, Request, Response

from core import health, security
from core.db import get_db, unique_slug
from core.fast_start import channel_from_path
from core.rtsp_probe import probe
from media import reconciler
from media import sync as mediamtx_sync

from .config import CHANNEL_VENDORS, PORT, VENDORS
from .helpers import (admin_camera, cameras_for_mediamtx, channel_path,
                      clear_node_cache, detect_codec, detect_sub_path,
                      mask_config, require_admin, resolve_ref)
from .models import CameraIn, NodeIn, NvrIn, ProbeIn, ScanIn, UserIn

# Prefiks nisbiy — create_app uni /api/v1 (asosiy) va /api (eski) ostida ulaydi.
router = APIRouter(prefix="/admin", tags=["admin"],
                   dependencies=[Depends(require_admin)])


# ---------- kameralar CRUD ----------

@router.get("/cameras")
def admin_list(request: Request, q: str = "", limit: int = 100, offset: int = 0):
    """Boshqaruv ro'yxati — qidiruv va sahifalash bilan.

    Kamera ko'p bo'lganda hammasini birdan yuborish ham tarmoqni, ham
    brauzerni bo'g'adi, shuning uchun bo'lib beriladi.
    """
    where, params = "", []
    if q.strip():
        needle = f"%{q.strip()}%"
        where = ("WHERE name LIKE ? OR region LIKE ? OR ip LIKE ? "
                 "OR slug LIKE ? OR note LIKE ?")
        params = [needle] * 5

    limit = max(1, min(limit, 500))
    with get_db() as db:
        total = db.execute(f"SELECT COUNT(*) FROM cameras {where}", params).fetchone()[0]
        rows = db.execute(
            f"SELECT * FROM cameras {where} ORDER BY region, name LIMIT ? OFFSET ?",
            params + [limit, max(0, offset)],
        ).fetchall()
    return {
        "total": total,
        "offset": offset,
        "cameras": [admin_camera(r, request) for r in rows],
    }


@router.post("/cameras", status_code=201)
def admin_create(cam: CameraIn, request: Request):
    cam.validate_complete()
    codec, transcode, resolution = detect_codec(cam, cam.password or "")
    sub_path, sub_codec = detect_sub_path(cam, cam.password or "")
    with get_db() as db:
        slug = unique_slug(db, f"{cam.region}_{cam.name}")
        try:
            db.execute(
                "INSERT INTO cameras (name, region, lat, lng, stream_url, slug, ip, "
                "port, username, password_enc, rtsp_path, sub_path, sub_codec, "
                "vendor, enabled, "
                "note, codec, resolution, transcode, always_on, node_id, external_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    cam.name.strip(), cam.region.strip(), cam.lat, cam.lng,
                    cam.stream_url.strip() if cam.source_type == "manual" else "",
                    slug,
                    cam.ip.strip() if cam.source_type == "rtsp" else "",
                    cam.port, cam.username.strip(),
                    security.encrypt(cam.password) if cam.password else "",
                    cam.rtsp_path.strip(), sub_path, sub_codec, cam.vendor,
                    int(cam.enabled),
                    cam.note.strip(), codec, resolution, int(transcode),
                    int(cam.always_on), cam.node_id, cam.external_id,
                ),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, f"external_id band: {cam.external_id}")
        row = db.execute("SELECT * FROM cameras WHERE slug = ?", (slug,)).fetchone()
    return admin_camera(row, request)


@router.put("/cameras/{ref}")
def admin_update(ref: str, cam: CameraIn, request: Request):
    cam.validate_complete()
    with get_db() as db:
        old = resolve_ref(db, ref)
        if old is None:
            raise HTTPException(404, "Kamera topilmadi")
        camera_id = old["id"]

        # Parol bo'sh qoldirilsa — eskisi saqlanadi.
        if cam.password:
            password_enc = security.encrypt(cam.password)
        elif cam.source_type == "rtsp":
            password_enc = old["password_enc"] or ""
        else:
            password_enc = ""

        slug = old["slug"]
        if cam.name.strip() != old["name"] or cam.region.strip() != old["region"]:
            slug = unique_slug(db, f"{cam.region}_{cam.name}", exclude_id=camera_id)

        # Kodekni qayta aniqlaymiz — kamera sozlamasi o'zgargan bo'lishi mumkin.
        password = cam.password or security.decrypt(password_enc)
        codec, transcode, resolution = detect_codec(cam, password)
        responded = bool(codec)
        if not responded:                   # kamera javob bermadi — eskisi qoladi
            codec, transcode = old["codec"] or "", bool(old["transcode"])
            resolution = old["resolution"] or ""

        sub_path, sub_codec = detect_sub_path(cam, password)
        if not sub_path and not responded:  # kamera javob bermadi — eskisi qoladi
            sub_path = old["sub_path"] or ""
            sub_codec = (old["sub_codec"] or "") if "sub_codec" in old.keys() else ""

        try:
            db.execute(
                "UPDATE cameras SET name=?, region=?, lat=?, lng=?, stream_url=?, "
                "slug=?, ip=?, port=?, username=?, password_enc=?, rtsp_path=?, "
                "sub_path=?, sub_codec=?, vendor=?, enabled=?, note=?, codec=?, "
                "resolution=?, "
                "transcode=?, always_on=?, node_id=?, external_id=? WHERE id=?",
                (
                    cam.name.strip(), cam.region.strip(), cam.lat, cam.lng,
                    cam.stream_url.strip() if cam.source_type == "manual" else "",
                    slug,
                    cam.ip.strip() if cam.source_type == "rtsp" else "",
                    cam.port, cam.username.strip(), password_enc,
                    cam.rtsp_path.strip(), sub_path, sub_codec, cam.vendor,
                    int(cam.enabled),
                    cam.note.strip(), codec, resolution, int(transcode),
                    int(cam.always_on), cam.node_id, cam.external_id, camera_id,
                ),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(409, f"external_id band: {cam.external_id}")
        row = db.execute("SELECT * FROM cameras WHERE id = ?", (camera_id,)).fetchone()
    return admin_camera(row, request)


@router.post("/cameras/detect-sub")
def admin_detect_sub():
    """Sub yo'li yo'q kameralarga past sifatli 2-oqimni topib beradi.

    Mavjud bazani bir bosishda to'ldirish uchun: har bir kamera uchun
    ishlab chiqaruvchi shablonidan sub yo'l hosil qilinadi va parallel
    tekshiriladi — faqat javob berganlari saqlanadi.
    """
    with get_db() as db:
        rows = db.execute(
            "SELECT * FROM cameras WHERE enabled = 1 AND ip IS NOT NULL "
            "AND ip != '' AND (sub_path IS NULL OR sub_path = '')"
        ).fetchall()

    def job(row) -> tuple[int, str, str]:
        main = (row["rtsp_path"] or "").strip()
        candidate = channel_path(row["vendor"] or "boshqa",
                                 channel_from_path(main), "sub")
        if candidate == main:
            return row["id"], "", ""
        result = probe(row["ip"], row["port"] or 554, candidate,
                       row["username"] or "",
                       security.decrypt(row["password_enc"]))
        if not result.get("ok"):
            return row["id"], "", ""
        return row["id"], candidate, result.get("codec", "")

    with ThreadPoolExecutor(max_workers=16) as pool:
        results = list(pool.map(job, rows))

    found = [(sub, codec, cam_id) for cam_id, sub, codec in results if sub]
    if found:
        with get_db() as db:
            db.executemany(
                "UPDATE cameras SET sub_path = ?, sub_codec = ? WHERE id = ?",
                found)
    return {"checked": len(rows), "found": len(found)}


@router.delete("/cameras/{ref}", status_code=204)
def admin_delete(ref: str):
    with get_db() as db:
        row = resolve_ref(db, ref)
        if row is None:
            raise HTTPException(404, "Kamera topilmadi")
        db.execute("DELETE FROM cameras WHERE id = ?", (row["id"],))


# ---------- NVR import va skaner ----------

def parse_channels(spec: str, limit: int = 512) -> list[int]:
    """"1-16" yoki "1,3,5-8" ni raqamlar ro'yxatiga aylantiradi."""
    numbers: list[int] = []
    for chunk in spec.replace(" ", "").split(","):
        if not chunk:
            continue
        if "-" in chunk:
            try:
                start, end = (int(v) for v in chunk.split("-", 1))
            except ValueError:
                raise HTTPException(400, f"Kanal oralig'i noto'g'ri: {chunk}")
            if start > end:
                start, end = end, start
            numbers.extend(range(start, end + 1))
        else:
            try:
                numbers.append(int(chunk))
            except ValueError:
                raise HTTPException(400, f"Kanal raqami noto'g'ri: {chunk}")

    unique = sorted({n for n in numbers if n > 0})
    if not unique:
        raise HTTPException(400, "Kanallar ko'rsatilmagan")
    if len(unique) > limit:
        raise HTTPException(400, f"Bir marta ko'pi bilan {limit} ta kanal")
    return unique


def spread_point(lat: float, lng: float, index: int, spread_m: int) -> tuple[float, float]:
    """Nuqtalarni spiral bo'ylab tarqatadi — markerlar ustma-ust tushmasin."""
    if spread_m <= 0 or index == 0:
        return lat, lng
    step = math.sqrt(index) * spread_m
    angle = index * 2.399963            # oltin burchak — bir tekis tarqaladi
    d_lat = (step * math.cos(angle)) / 111_320
    d_lng = (step * math.sin(angle)) / (111_320 * max(0.2, math.cos(math.radians(lat))))
    return round(lat + d_lat, 6), round(lng + d_lng, 6)


@router.post("/nvr/import")
def admin_nvr_import(body: NvrIn):
    """Registratordagi kanallarni birdaniga kameralarga aylantiradi."""
    channels = parse_channels(body.channels)
    prefix = body.name_prefix.strip() or body.region.strip()

    planned = []
    for index, channel in enumerate(channels):
        lat, lng = spread_point(body.lat, body.lng, index, body.spread_m)
        planned.append({
            "channel": channel,
            "name": f"{prefix} {channel}-kanal",
            "rtsp_path": channel_path(body.vendor, channel, body.stream),
            # Video devor uchun past sifatli 2-oqim — faqat tekshiruvdan
            # o'tsa saqlanadi (ba'zi NVR kanallarida sub yo'q bo'ladi).
            "sub_path": (channel_path(body.vendor, channel, "sub")
                         if body.stream == "main" else ""),
            "lat": lat, "lng": lng,
        })

    # Tekshirish parallel ketadi — 64 ta kanalni ketma-ket tekshirish
    # bir necha daqiqa oladi, parallel esa bir necha soniya. Asosiy va
    # sub oqimlar bitta hovuzda birga tekshiriladi.
    results: dict[tuple[int, str], dict] = {}
    if body.probe:
        jobs = [(item["channel"], "main", item["rtsp_path"]) for item in planned]
        jobs += [(item["channel"], "sub", item["sub_path"])
                 for item in planned if item["sub_path"]]
        with ThreadPoolExecutor(max_workers=16) as pool:
            futures = {
                pool.submit(probe, body.ip, body.port, path,
                            body.username, body.password): (channel, kind)
                for channel, kind, path in jobs
            }
            for future in as_completed(futures):
                results[futures[future]] = future.result()

    for item in planned:
        result = results.get((item["channel"], "main"))
        item["ok"] = result["ok"] if result else None
        item["codec"] = result.get("codec", "") if result else ""
        item["resolution"] = result.get("resolution", "") if result else ""
        item["transcode"] = bool(result.get("needs_transcode")) if result else False
        item["message"] = result["message"] if result else "tekshirilmadi"
        if body.probe:
            sub = results.get((item["channel"], "sub"))
            if not (sub and sub.get("ok")):
                item["sub_path"] = ""

    if body.dry_run:
        return {"planned": planned, "created": 0,
                "reachable": sum(1 for p in planned if p["ok"])}

    # Javob bermagan kanallar saqlanmaydi — NVR'da bo'sh slotlar ko'p bo'ladi.
    keep = [p for p in planned if p["ok"] or not body.probe]
    password_enc = security.encrypt(body.password) if body.password else ""
    created = 0
    with get_db() as db:
        for item in keep:
            slug = unique_slug(db, f"{body.region}_{item['name']}")
            db.execute(
                "INSERT INTO cameras (name, region, lat, lng, stream_url, slug, ip, "
                "port, username, password_enc, rtsp_path, sub_path, vendor, enabled, "
                "note, codec, resolution, transcode, always_on, node_id) "
                "VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?)",
                (item["name"], body.region.strip(), item["lat"], item["lng"], slug,
                 body.ip.strip(), body.port, body.username.strip(), password_enc,
                 item["rtsp_path"], item["sub_path"], body.vendor, int(body.enabled),
                 f"{body.ip} · {item['channel']}-kanal",
                 item["codec"], item["resolution"], int(item["transcode"]),
                 body.node_id),
            )
            created += 1

    return {"planned": planned, "created": created,
            "skipped": len(planned) - created,
            "reachable": sum(1 for p in planned if p["ok"])}


def _probe_many(ip: str, port: int, jobs: dict, username: str,
                password: str) -> dict:
    """Bir nechta RTSP yo'lni parallel tekshiradi: {kalit: probe natijasi}."""
    results: dict = {}
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = {
            pool.submit(probe, ip, port, path, username, password): key
            for key, path in jobs.items()
        }
        for future in as_completed(futures):
            results[futures[future]] = future.result()
    return results


@router.post("/scan")
def admin_scan(body: ScanIn):
    """Qurilmani o'zi aniqlaydi: turi (kamera/NVR), shabloni va jonli kanallari.

    Oddiy foydalanuvchi RTSP yo'lini ham, kanal raqamlarini ham bilmaydi —
    IP va login/parol yetarli. Skaner mashhur shablonlarni sinab, qaysi
    biri ishlashini topadi, so'ng kanallarni 8 talik bloklarda tekshiradi
    va bo'sh blok kelganda to'xtaydi.
    """
    ip, port = body.ip.strip(), body.port
    user, pw = body.username.strip(), body.password
    if not pw and body.camera_id:
        with get_db() as db:
            row = db.execute("SELECT password_enc FROM cameras WHERE id = ?",
                             (body.camera_id,)).fetchone()
        if row:
            pw = security.decrypt(row["password_enc"])

    # 1) Shablonni aniqlash — har bir ishlab chiqaruvchining 1-kanali.
    candidates = {v: channel_path(v, 1, "main") for v in CHANNEL_VENDORS}
    candidates["boshqa"] = "/stream1"          # bitta oqimli oddiy kameralar
    first = _probe_many(ip, port, candidates, user, pw)

    # Hech biri ochilmadi-yu, parol xatosi bor — avval shuni aytamiz.
    if not any(r["ok"] for r in first.values()):
        for stage in ("parol", "oqim", "rtsp", "tarmoq"):
            hit = next((r for r in first.values() if r.get("stage") == stage), None)
            if hit:
                return {"found": False, "message": hit["message"]}
        return {"found": False, "message": "Qurilma javob bermadi"}

    vendor = next(v for v in [*CHANNEL_VENDORS, "boshqa"]
                  if first.get(v, {}).get("ok"))
    vendor_name = next((v["name"] for v in VENDORS if v["id"] == vendor), vendor)

    def entry(channel: int, result: dict) -> dict:
        return {
            "channel": channel,
            "rtsp_path": channel_path(vendor, channel, "main"),
            "codec": result.get("codec", ""),
            "needs_transcode": bool(result.get("needs_transcode")),
        }

    channels = [entry(1, first[vendor])]

    # 2) Kanallarni sanash — faqat kanal raqamini biladigan shablonlarda.
    if vendor != "boshqa":
        start = 2
        while start <= body.max_channels:
            block = range(start, min(start + 8, body.max_channels + 1))
            jobs = {c: channel_path(vendor, c, "main") for c in block}
            results = _probe_many(ip, port, jobs, user, pw)
            live = [c for c in sorted(results) if results[c]["ok"]]
            channels.extend(entry(c, results[c]) for c in live)
            if not live:                       # bo'sh blok — qurilma tugadi
                break
            start += 8

    return {
        "found": True,
        "vendor": vendor,
        "vendor_name": vendor_name,
        "device": "nvr" if len(channels) > 1 else "camera",
        "channels": channels,
    }


@router.post("/probe")
def admin_probe(body: ProbeIn):
    """Kamera bilan aloqani va login/parolni tekshiradi."""
    password = body.password or ""
    if not password and body.camera_id:
        with get_db() as db:
            row = db.execute(
                "SELECT password_enc FROM cameras WHERE id = ?", (body.camera_id,)
            ).fetchone()
        if row:
            password = security.decrypt(row["password_enc"])
    return probe(body.ip.strip(), body.port, body.rtsp_path.strip(),
                 body.username.strip(), password)


# ---------- foydalanuvchilar ----------
#
# Rollar: 'admin' — hammasini boshqaradi (shu bo'lim ham faqat unga ochiq);
# 'operator' — faqat o'ziga biriktirilgan hududlardagi kameralarni ko'radi
# (xarita ro'yxati, oqim va surat shu ro'yxat bilan cheklanadi).

def _user_view(db, row) -> dict:
    return {
        "id": row["id"], "username": row["username"], "role": row["role"],
        "created_at": row["created_at"],
        "regions": (security.user_regions(db, row["id"])
                    if row["role"] == "operator" else []),
    }


def _check_password(password: str | None, required: bool) -> None:
    if required and not password:
        raise HTTPException(400, "Parol kiritilmagan")
    if password and len(password) < 6:
        raise HTTPException(400, "Parol kamida 6 belgidan iborat bo'lsin")


@router.get("/users")
def admin_users():
    with get_db() as db:
        rows = db.execute(
            "SELECT id, username, role, created_at FROM admins ORDER BY id"
        ).fetchall()
        return {"users": [_user_view(db, r) for r in rows]}


@router.post("/users", status_code=201)
def admin_user_create(body: UserIn):
    _check_password(body.password, required=True)
    pw_hash, salt = security.hash_password(body.password)
    with get_db() as db:
        try:
            cur = db.execute(
                "INSERT INTO admins (username, pw_hash, pw_salt, role) "
                "VALUES (?, ?, ?, ?)",
                (body.username.strip(), pw_hash, salt, body.role),
            )
        except sqlite3.IntegrityError:
            raise HTTPException(400, "Bunday login allaqachon bor")
        security.set_user_regions(
            db, cur.lastrowid, body.regions if body.role == "operator" else [])
        row = db.execute("SELECT id, username, role, created_at FROM admins "
                         "WHERE id = ?", (cur.lastrowid,)).fetchone()
        return _user_view(db, row)


@router.put("/users/{user_id}")
def admin_user_update(user_id: int, body: UserIn):
    _check_password(body.password, required=False)
    with get_db() as db:
        old = db.execute("SELECT * FROM admins WHERE id = ?",
                         (user_id,)).fetchone()
        if old is None:
            raise HTTPException(404, "Foydalanuvchi topilmadi")
        if old["role"] == "admin" and body.role != "admin":
            admins = db.execute("SELECT COUNT(*) FROM admins "
                                "WHERE role = 'admin'").fetchone()[0]
            if admins <= 1:
                raise HTTPException(400, "Oxirgi adminni operator qilib bo'lmaydi")
        try:
            db.execute("UPDATE admins SET username = ?, role = ? WHERE id = ?",
                       (body.username.strip(), body.role, user_id))
        except sqlite3.IntegrityError:
            raise HTTPException(400, "Bunday login allaqachon bor")
        if body.password:
            pw_hash, salt = security.hash_password(body.password)
            db.execute("UPDATE admins SET pw_hash = ?, pw_salt = ? WHERE id = ?",
                       (pw_hash, salt, user_id))
            # Parol almashdi — eski sessiyalar bekor.
            db.execute("DELETE FROM sessions WHERE admin_id = ?", (user_id,))
        security.set_user_regions(
            db, user_id, body.regions if body.role == "operator" else [])
        row = db.execute("SELECT id, username, role, created_at FROM admins "
                         "WHERE id = ?", (user_id,)).fetchone()
        return _user_view(db, row)


@router.delete("/users/{user_id}", status_code=204)
def admin_user_delete(user_id: int, me=Depends(require_admin)):
    if me["id"] == user_id:
        raise HTTPException(400, "O'z hisobingizni o'chira olmaysiz")
    with get_db() as db:
        row = db.execute("SELECT role FROM admins WHERE id = ?",
                         (user_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Foydalanuvchi topilmadi")
        if row["role"] == "admin":
            admins = db.execute("SELECT COUNT(*) FROM admins "
                                "WHERE role = 'admin'").fetchone()[0]
            if admins <= 1:
                raise HTTPException(400, "Oxirgi admin o'chirilmaydi")
        db.execute("DELETE FROM admins WHERE id = ?", (user_id,))
        db.execute("DELETE FROM sessions WHERE admin_id = ?", (user_id,))
        db.execute("DELETE FROM user_regions WHERE user_id = ?", (user_id,))


# ---------- MediaMTX ----------

# ---------- MediaMTX tugunlari ----------

@router.get("/nodes")
def admin_nodes():
    """Tugunlar ro'yxati: kameralar soni, salomatlik va ish ko'rsatkichlari.

    `status`: online — API tirik va muzlagan oqim yo'q; degraded — API tirik,
    lekin kamida bitta faol oqim muzlagan; offline — API javob bermayapti
    (yangi kameralarni bunday tugunga biriktirmang).
    """
    with get_db() as db:
        rows = db.execute(
            "SELECT n.*, (SELECT COUNT(*) FROM cameras c WHERE c.node_id = n.id) "
            "AS cameras FROM nodes n ORDER BY n.id"
        ).fetchall()
    nodes = []
    for row in rows:
        node = dict(row)
        runtime = mediamtx_sync.node_runtime(row["api_base"])
        stalled = reconciler.stalled_count(row["id"])
        node["online"] = runtime is not None
        node["stalled"] = stalled
        node["runtime"] = runtime
        node["status"] = ("offline" if runtime is None else
                          "degraded" if stalled else "online")
        nodes.append(node)
    return {"nodes": nodes}


@router.post("/nodes", status_code=201)
def admin_node_create(body: NodeIn):
    with get_db() as db:
        cur = db.execute(
            "INSERT INTO nodes (name, api_base, public_host, rtsp_port, "
            "hls_port, webrtc_port, enabled) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (body.name.strip(), body.api_base.strip().rstrip("/"),
             body.public_host.strip(), body.rtsp_port, body.hls_port,
             body.webrtc_port, int(body.enabled)),
        )
        row = db.execute("SELECT * FROM nodes WHERE id = ?",
                         (cur.lastrowid,)).fetchone()
    clear_node_cache()
    return dict(row)


@router.put("/nodes/{node_id}")
def admin_node_update(node_id: int, body: NodeIn):
    with get_db() as db:
        cur = db.execute(
            "UPDATE nodes SET name=?, api_base=?, public_host=?, rtsp_port=?, "
            "hls_port=?, webrtc_port=?, enabled=? WHERE id=?",
            (body.name.strip(), body.api_base.strip().rstrip("/"),
             body.public_host.strip(), body.rtsp_port, body.hls_port,
             body.webrtc_port, int(body.enabled), node_id),
        )
        if cur.rowcount == 0:
            raise HTTPException(404, "Tugun topilmadi")
        row = db.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
    clear_node_cache()
    return dict(row)


@router.delete("/nodes/{node_id}", status_code=204)
def admin_node_delete(node_id: int):
    if node_id == 1:
        raise HTTPException(400, "Asosiy tugunni o'chirib bo'lmaydi")
    with get_db() as db:
        used = db.execute("SELECT COUNT(*) FROM cameras WHERE node_id = ?",
                          (node_id,)).fetchone()[0]
        if used:
            raise HTTPException(400, f"Tugunda {used} ta kamera bor — avval "
                                     f"ularni boshqa tugunga o'tkazing")
        cur = db.execute("DELETE FROM nodes WHERE id = ?", (node_id,))
        if cur.rowcount == 0:
            raise HTTPException(404, "Tugun topilmadi")
    clear_node_cache()


@router.get("/nodes/{node_id}/config")
def admin_node_config(node_id: int, request: Request):
    """Tugun mashinasiga qo'yiladigan tayyor mediamtx.yml.

    Ichida parol yo'q (kamera yo'llarini markaz API orqali yuboradi),
    shuning uchun ochiq qaytariladi. Auth manzili — backend'ning tugun
    ko'radigan manzili; kerak bo'lsa STREAM_AUTH_URL bilan almashtiring.
    """
    with get_db() as db:
        row = db.execute("SELECT * FROM nodes WHERE id = ?", (node_id,)).fetchone()
    if row is None:
        raise HTTPException(404, "Tugun topilmadi")
    auth_url = f"http://{request.url.hostname}:{PORT}/api/auth/stream"
    return Response(
        mediamtx_sync.build_config([], auth_url=auth_url, node=dict(row)),
        media_type="text/plain; charset=utf-8",
    )


@router.get("/status")
def admin_status():
    """Tizim salomatligi bir qarashda — 5000 kamerani ko'z bilan emas,
    raqam bilan kuzatish uchun: MediaMTX tirikmi, health sweep intervalga
    sig'ayaptimi, qaysi faol oqimlar muzlagan, tugunlar qay ahvolda."""
    with get_db() as db:
        node_rows = db.execute(
            "SELECT id, name, api_base FROM nodes WHERE enabled = 1 ORDER BY id"
        ).fetchall()
    nodes = []
    for row in node_rows:
        runtime = mediamtx_sync.node_runtime(row["api_base"])
        stalled = reconciler.stalled_count(row["id"])
        nodes.append({
            "name": row["name"],
            "status": ("offline" if runtime is None else
                       "degraded" if stalled else "online"),
            "ready": runtime["ready"] if runtime else 0,
            "readers": runtime["readers"] if runtime else 0,
            "stalled": stalled,
        })
    return {
        "mediamtx": mediamtx_sync.api_available(),
        "health": health.sweep_stats(),
        "stalled": sorted(reconciler.stalled_paths()),
        "nodes": nodes,
    }


@router.get("/events")
def admin_events(limit: int = 100):
    """Media qatlamining so'nggi hodisalari: oqim muzladi/tiklandi,
    MediaMTX qayta ishga tushdi. Kamera uzilish tarixi stats_event'da."""
    with get_db() as db:
        rows = db.execute(
            "SELECT ts, kind, ip, port, slug, detail FROM events "
            "ORDER BY id DESC LIMIT ?", (max(1, min(limit, 500)),),
        ).fetchall()
    return {"events": [dict(r) for r in rows]}


@router.post("/mediamtx/sync")
def admin_sync():
    """mediamtx.yml faylini qayta yozadi va har bir tugunni jonli yangilaydi.

    Kameralar tugun bo'yicha ajratib yuboriladi — aks holda boshqa tugunga
    biriktirilgan kameralar lokal MediaMTX'ga ham tushib, 30 soniyadan
    keyin reconciler ularni qaytarib o'chirardi (keraksiz tebranish).
    """
    with get_db() as db:
        cameras = cameras_for_mediamtx(db)
        nodes = [dict(r) for r in db.execute(
            "SELECT * FROM nodes WHERE enabled = 1 ORDER BY id").fetchall()]
    written = mediamtx_sync.write_config(cameras)
    if not nodes:
        nodes = [{"id": 1, "name": "Asosiy", "api_base": None}]

    results = []
    for node in nodes:
        node_cams = [c for c in cameras if (c.get("node_id") or 1) == node["id"]]
        pushed = mediamtx_sync.push_to_api(node_cams, api_base=node["api_base"])
        results.append({"node": node["name"], **pushed})

    ok = all(r["ok"] for r in results)
    message = (results[0]["message"] if len(results) == 1 else
               " · ".join(f"{r['node']}: {r['message']}" for r in results))
    return {
        "written": written,
        "config_path": str(mediamtx_sync.CONFIG_PATH),
        "live": {
            "ok": ok, "message": message,
            "added": sum(r["added"] for r in results),
            "updated": sum(r["updated"] for r in results),
            "removed": sum(r["removed"] for r in results),
            "nodes": results,
        },
    }


@router.get("/mediamtx/config")
def admin_config_preview():
    with get_db() as db:
        cameras = cameras_for_mediamtx(db)
    return {
        # Parollar yashiriladi — faylga esa ochiq holda yoziladi (MediaMTX uchun).
        "text": mask_config(mediamtx_sync.build_config(cameras)),
        "api_available": mediamtx_sync.api_available(),
        "transcoding": sum(1 for c in cameras if c["transcode"] and c["enabled"]),
        "gpu": mediamtx_sync.has_nvenc(),
    }

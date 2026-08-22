"""Nigoh — routelar o'rtasida umumiy yordamchilar.

Bu yerda ikkita "tarjima" qatlami yashaydi:

  * baza qatori → brauzerga xavfsiz ko'rinish (public/admin);
  * baza qatori → MediaMTX tushunadigan ko'rinish.
"""
import hmac
import re
import time

from fastapi import HTTPException, Request

from core import health, security
from core.db import get_db
from core.fast_start import channel_from_path
from core.rtsp_probe import probe
from media import reconciler
from media import sync as mediamtx_sync

from .config import API_KEY, HLS_PORT, MEDIA_BASE, MEDIA_HOST, WEBRTC_PORT
from .models import CameraIn


def media_host(request: Request) -> str:
    """MediaMTX qaysi manzilda ekani — brauzer shu manzilga ulanadi."""
    if MEDIA_HOST:
        return MEDIA_HOST
    host = request.url.hostname or "localhost"
    return "localhost" if host == "0.0.0.0" else host


def resolve_ref(db, ref: str):
    """Kamera qatori `ref` bo'yicha: '123' — ichki id, 'ext:...' — tashqi id.

    Asosiy tizim o'z identifikatori bilan murojaat qila oladi — mapping
    jadval yuritish shart emas. Topilmasa None.
    """
    ref = (ref or "").strip()
    if ref.startswith("ext:"):
        return db.execute("SELECT * FROM cameras WHERE external_id = ?",
                          (ref[4:],)).fetchone()
    if ref.isdigit():
        return db.execute("SELECT * FROM cameras WHERE id = ?",
                          (int(ref),)).fetchone()
    return None


# ---------- MediaMTX tugunlari ----------

_node_cache: dict[int, tuple[float, dict | None]] = {}
_NODE_TTL = 30.0


def node_info(node_id: int | None) -> dict | None:
    """Tugun ma'lumoti (qisqa keshlanadi).

    1-tugun — backend bilan bitta mashinadagi asosiy MediaMTX: uning uchun
    None qaytadi va muhit sozlamalari (portlar, so'rov hosti) ishlatiladi,
    ya'ni bitta tugunli rejim bazaga umuman murojaat qilmaydi.
    """
    nid = node_id or 1
    if nid == 1:
        return None
    now = time.monotonic()
    cached = _node_cache.get(nid)
    if cached and cached[0] > now:
        return cached[1]
    with get_db() as db:
        row = db.execute("SELECT * FROM nodes WHERE id = ?", (nid,)).fetchone()
    info = dict(row) if row else None
    _node_cache[nid] = (now + _NODE_TTL, info)
    return info


def clear_node_cache() -> None:
    """Tugun tahrirlanganda kesh darhol yangilansin."""
    _node_cache.clear()


def stream_urls(row, request: Request, hevc_ok: bool = False,
                quality: str = "") -> dict:
    """Kameraning oqim manzillari — faqat kerak bo'lganda so'raladi.

    `hevc_ok` — brauzer H.265 ni o'zi o'qiy oladi. Shunday bo'lsa, H.265
    kamera o'girilmaydi: oddiy `<kamera>` yo'lining o'zi xom oqimni beradi
    va GPU umuman ishlatilmaydi (mode: "raw"). Bunda WebRTC amalda
    ishlamaydi (u H.265 ni bilmaydi) — brauzer HLS'ga o'tadi.

    `quality="sub"` — past sifatli ikkinchi oqim (video devor setkasi
    uchun). Kamerada sub yo'l bo'lmasa, jimgina asosiy oqim qaytadi.
    """
    host = media_host(request)
    if not row["ip"]:
        return {"stream_url": row["stream_url"] or "", "webrtc_url": "",
                "mode": "manual"}

    # Kamera boshqa tugunga biriktirilgan bo'lsa, brauzer o'sha tugunga
    # to'g'ridan-to'g'ri ulanadi — trafik markaz orqali aylanib yurmaydi.
    hls_port, webrtc_port = HLS_PORT, WEBRTC_PORT
    node = node_info(row["node_id"])
    if node:
        host = node["public_host"] or host
        hls_port, webrtc_port = node["hls_port"], node["webrtc_port"]

    # Xom yo'l — MediaMTX kameradan to'g'ridan-to'g'ri oladi, FFmpeg yo'q.
    #
    # O'girish ikki holatda kerak:
    #   1) brauzer H.265 ni uddalay olmasa;
    #   2) "tez ochilsin" belgilangan bo'lsa — kameralarning keyframe oralig'i
    #      2-4 soniya, o'girilgan oqimda esa 1,2 soniya, ya'ni ikki barobar
    #      tez ochiladi. Buning narxi: doimiy FFmpeg va GPU.
    slug = row["slug"]
    if quality == "sub" and row["sub_path"]:
        slug += mediamtx_sync.SUB_SUFFIX
        mode = "sub"
        # Sub-oqim odatda H.264 bo'ladi — lekin har doim emas: H.265
        # kameraning ikkinchi oqimi ham H.265 chiqishi mumkin (o'lchovda
        # shunday kameralar uchradi). Xom H.265 brauzerga berilsa tasvir
        # chiqmaydi yoki birinchi kadrda qotib qoladi, shuning uchun sub
        # ham asosiy oqim bilan bir xil qoidada o'giriladi. `mode` "sub"
        # bo'lib qolaveradi: ishlamasa pleyer avvalgidek asosiyga o'tadi.
        if row["transcode"] and not hevc_ok:
            slug += mediamtx_sync.TRANSCODE_SUFFIX
    elif row["transcode"] and (not hevc_ok or row["always_on"]):
        slug += mediamtx_sync.TRANSCODE_SUFFIX
        mode = "transcode"
    else:
        mode = "raw" if row["transcode"] else "direct"

    # Chipta shu yo'lga bog'langan va muddatli — MediaMTX'ni backend
    # tekshiradi (routes_auth.stream_auth), chiptasiz oqim ochilmaydi.
    token = security.stream_token(slug)

    # HTTPS proksi rejimi: hamma oqim bitta domen ostidan yuradi, portlar
    # tashqariga ko'rinmaydi. Uzoq tugunlar bunga kirmaydi — ular o'z
    # manzilida qoladi.
    if MEDIA_BASE and not node:
        return {
            "stream_url": f"{MEDIA_BASE}/hls/{slug}/index.m3u8?token={token}",
            "webrtc_url": f"{MEDIA_BASE}/whep/{slug}/whep?token={token}",
            "mode": mode,
        }
    return {
        "stream_url": f"http://{host}:{hls_port}/{slug}/index.m3u8?token={token}",
        # WebRTC ancha tez ochiladi — brauzer avval shuni sinaydi.
        "webrtc_url": f"http://{host}:{webrtc_port}/{slug}/whep?token={token}",
        "mode": mode,
    }


def camera_state(row) -> str:
    """Kameraning yagona holati — sochilgan kuzatuvlar bitta maydonda.

    disabled — admin o'chirib qo'ygan;
    unknown  — IP'siz (tayyor oqim) yoki hali tekshirilmagan;
    offline  — tarmoqdan javob yo'q (TCP tekshiruv);
    stalled  — port ochiq, lekin faol oqimga bayt kelmayapti (reconciler);
    online   — hammasi joyida.
    """
    if not row["enabled"]:
        return "disabled"
    if not row["ip"]:
        return "unknown"
    slug = row["slug"] or ""
    variants = {slug, slug + mediamtx_sync.SUB_SUFFIX,
                slug + mediamtx_sync.TRANSCODE_SUFFIX}
    for display in reconciler.stalled_paths():
        if display.split("@", 1)[0] in variants:
            return "stalled"
    alive = health.online(row["ip"], row["port"])
    if alive is None:
        return "unknown"
    return "online" if alive else "offline"


def public_camera(row, request: Request) -> dict:
    """Brauzerga yuboriladigan xavfsiz ko'rinish — parol/IP yo'q."""
    data = {
        "id": row["id"],
        "name": row["name"],
        "region": row["region"],
        "lat": row["lat"],
        "lng": row["lng"],
    }
    data.update(stream_urls(row, request))
    return data


def admin_camera(row, request: Request) -> dict:
    """Admin ko'rinishi — parolning o'zi emas, bor-yo'qligi qaytariladi."""
    data = public_camera(row, request)
    data.update({
        "slug": row["slug"],
        "external_id": row["external_id"] or "",
        "state": camera_state(row),
        "resolution": row["resolution"] or "",
        "source_type": "rtsp" if row["ip"] else "manual",
        "ip": row["ip"] or "",
        "port": row["port"] or 554,
        "username": row["username"] or "",
        "has_password": bool(row["password_enc"]),
        "rtsp_path": row["rtsp_path"] or "",
        "sub_path": row["sub_path"] or "",
        "sub_codec": (row["sub_codec"] or "") if "sub_codec" in row.keys() else "",
        "node_id": row["node_id"] or 1,
        "vendor": row["vendor"] or "boshqa",
        "enabled": bool(row["enabled"]),
        "note": row["note"] or "",
        "raw_stream_url": row["stream_url"] or "",
        "model": (row["model"] or "") if "model" in row.keys() else "",
        "firmware": (row["firmware"] or "") if "firmware" in row.keys() else "",
        "last_seen": (row["last_seen"] or "") if "last_seen" in row.keys() else "",
        "codec": row["codec"] or "",
        # SDP'dagi kadr tezligi (0 — kamera bermagan). "25 fps deb
        # sozlangan kamera 8 fps beryapti" degan xulosa shu maydonsiz
        # chiqmaydi.
        "fps": (float(row["fps"] or 0.0) if "fps" in row.keys() else 0.0),
        "transcode": bool(row["transcode"]),
        "always_on": bool(row["always_on"]),
    })
    if row["ip"]:
        cred = row["username"] or ""
        if cred and row["password_enc"]:
            cred += ":•••"
        prefix = f"{cred}@" if cred else ""
        path = "/" + (row["rtsp_path"] or "").lstrip("/")
        data["rtsp_preview"] = f"rtsp://{prefix}{row['ip']}:{row['port'] or 554}{path}"
    return data


def mask_config(text: str) -> str:
    """Konfiguratsiyadagi ochiq parollarni yashiradi — brauzerga shu ketadi."""
    return re.sub(r"(rtsp://[^:/@\s]+):[^@\s]+@", r"\1:•••@", text)


def api_key_ok(request: Request) -> bool:
    """Server-to-server kirish: `X-API-Key` sarlavhasi to'g'rimi.

    Tashqi backend (o'z foydalanuvchi/rol tizimi bor tizim) Nigoh'ga shu
    kalit bilan to'liq kiradi — ruxsatlarni o'zi hal qilib, bu yerdan
    faqat chiptali oqim manzillari va kamera boshqaruvini oladi.
    """
    if not API_KEY:
        return False
    supplied = request.headers.get("x-api-key", "")
    return bool(supplied) and hmac.compare_digest(supplied, API_KEY)


def current_user(request: Request):
    """Debug UI sessiyasidagi foydalanuvchi, bo'lmasa None."""
    token = request.cookies.get(security.SESSION_COOKIE)
    with get_db() as db:
        return security.session_admin(db, token)


def require_admin(request: Request):
    """Boshqaruv endpointlari uchun: admin roli yoki to'g'ri API kalit."""
    if api_key_ok(request):
        return {"id": 0, "username": "api", "role": "admin"}
    user = current_user(request)
    if user is None:
        raise HTTPException(401, "Avval super-admin sifatida kiring")
    if user["role"] != "admin":
        raise HTTPException(403, "Bu bo'lim faqat admin uchun")
    return user


def camera_for_mediamtx(row) -> dict | None:
    """Bitta kamerani MediaMTX tushunadigan ko'rinishga o'tkazadi."""
    if not row["ip"]:
        return None
    return {
        "slug": row["slug"], "ip": row["ip"], "port": row["port"] or 554,
        "rtsp_path": row["rtsp_path"] or "/", "username": row["username"] or "",
        "password": security.decrypt(row["password_enc"]),
        "enabled": bool(row["enabled"]),
        "transcode": bool(row["transcode"]),
        "always_on": bool(row["always_on"]),
        "sub_path": row["sub_path"] or "",
        "node_id": row["node_id"] or 1,
    }


def cameras_for_mediamtx(db) -> list[dict]:
    rows = db.execute("SELECT * FROM cameras WHERE ip IS NOT NULL AND ip != ''").fetchall()
    return [c for c in (camera_for_mediamtx(r) for r in rows) if c]


def channel_path(vendor: str, channel: int, stream: str) -> str:
    """Kanal raqamidan ishlab chiqaruvchiga mos RTSP yo'lini quradi."""
    sub = stream == "sub"
    if vendor == "hikvision":
        # 101 = 1-kanal asosiy, 102 = 1-kanal qo'shimcha oqim
        return f"/Streaming/Channels/{channel}0{2 if sub else 1}"
    if vendor in ("dahua", "amcrest"):
        return f"/cam/realmonitor?channel={channel}&subtype={1 if sub else 0}"
    if vendor == "uniview":
        return f"/unicast/c{channel}/s{2 if sub else 1}/live"
    if vendor == "reolink":
        return f"/h264Preview_{channel:02d}_{'sub' if sub else 'main'}"
    if vendor == "axis":
        return f"/axis-media/media.amp?camera={channel}"
    if vendor == "holowits":
        return f"/LiveMedia/ch{channel}/Media{2 if sub else 1}"
    return f"/stream{2 if sub else 1}"


def detect_sub_path(cam: CameraIn, password: str) -> tuple[str, str]:
    """Past sifatli ikkinchi oqim yo'lini va kodegini topadi.

    Admin qiymat kiritgan bo'lsa — o'sha saqlanadi (kodek tekshiruvda
    aniqlanadi). Kiritmagan bo'lsa ishlab chiqaruvchi shablonidan hosil
    qilinadi va tekshiriladi: javob bermagan yo'l saqlanmaydi (ba'zi
    NVR'lar ikkinchi oqimni bermaydi — masalan, ayrim Holowits
    kanallarida Media2 xato beradi).

    Qaytaradi: (sub_path, sub_codec). Kodek alohida saqlanadi — devor
    plitkasida "H265" yorlig'i ko'rinib, aslida H.264 sub ko'rsatilayotgan
    chalkashlik bo'lmasin.
    """
    if cam.source_type != "rtsp" or not cam.ip.strip():
        return (cam.sub_path or "").strip(), ""

    if cam.sub_path is not None:
        sub = cam.sub_path.strip()
        if not sub:
            return "", ""
        result = probe(cam.ip.strip(), cam.port, sub,
                       cam.username.strip(), password)
        return sub, result.get("codec", "") if result.get("ok") else ""

    candidate = channel_path(cam.vendor, channel_from_path(cam.rtsp_path), "sub")
    if candidate == cam.rtsp_path.strip():
        return "", ""                      # asosiy oqimning o'zi sub ekan
    result = probe(cam.ip.strip(), cam.port, candidate,
                   cam.username.strip(), password)
    if not result.get("ok"):
        return "", ""
    return candidate, result.get("codec", "")


def detect_codec(cam: CameraIn, password: str) -> tuple[str, bool, str, float]:
    """Saqlashdan oldin kamera kodegi, o'lchami va kadr tezligini aniqlaydi.

    Qaytaradi: (codec, needs_transcode, resolution, fps).

    Kamera javob bermasa bo'sh qaytaradi — bu saqlashga to'sqinlik qilmaydi.
    `fps` 0 bo'lishi ham normal: ba'zi kameralar SDP'da kadr tezligini
    umuman bermaydi.
    """
    if cam.source_type != "rtsp" or not cam.ip.strip():
        return "", False, "", 0.0
    result = probe(cam.ip.strip(), cam.port, cam.rtsp_path.strip(),
                   cam.username.strip(), password)
    if not result.get("ok"):
        return "", False, "", 0.0
    return (result.get("codec", ""), bool(result.get("needs_transcode")),
            result.get("resolution", ""), float(result.get("fps") or 0.0))

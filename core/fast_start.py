"""Nigoh — kamerani "juda tez ochilish"ga yordam beruvchi qism.

Ochilish vaqtining asosiy qismi kameraning navbatdagi keyframe'ini kutishga
ketadi (odatda 2-4 soniya). Bu modul ikkita usul bilan shu kutishni yo'qotadi;
ikkalasi ham ixtiyoriy va xatoga chidamli — kamera qo'llamasa jim o'tiladi:

1. ONVIF SetSynchronizationPoint — tomoshabin ulanganda kameradan darhol
   keyframe so'raladi. Hikvision/Dahua va aksariyat ONVIF kameralar
   qo'llaydi; javob ~0,3-0,5 soniyada keladi, GOP qancha uzun bo'lishidan
   qat'i nazar.

2. JPEG surat (snapshot) — video ulangunicha brauzerga kameraning hozirgi
   surati ko'rsatiladi. His qilinadigan ochilish ~100 ms ga tushadi.
   Suratlar qisqa muddat keshlanadi, kamera bosim ostida qolmaydi.
"""
import base64
import hashlib
import os
import re
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from functools import lru_cache
from xml.sax.saxutils import escape

TIMEOUT = 3.0

# ONVIF va snapshot odatda kameraning web-portida bo'ladi (554 emas).
HTTP_PORT = int(os.environ.get("CAMERA_HTTP_PORT", "80"))


# ---------- RTSP yo'lidan kanal raqamini ajratish ----------

_CHANNEL_PATTERNS = (
    r"/Channels/(\d+)",      # hikvision: /Streaming/Channels/101 -> 1
    r"channel=(\d+)",        # dahua/amcrest/axis param
    r"Preview_(\d+)_",       # reolink: /h264Preview_01_main -> 1
    r"camera=(\d+)",         # axis
    r"/c(\d+)/",             # uniview: /unicast/c3/s1/live -> 3
    r"/ch(\d+)/",            # holowits: /LiveMedia/ch2/Media1 -> 2
)


def channel_from_path(rtsp_path: str) -> int:
    """NVR'dagi kanal raqami — snapshot va ONVIF profilini tanlash uchun."""
    path = rtsp_path or ""
    for pattern in _CHANNEL_PATTERNS:
        match = re.search(pattern, path, re.IGNORECASE)
        if match:
            n = int(match.group(1))
            if pattern is _CHANNEL_PATTERNS[0] and n >= 100:
                n //= 100        # 101 -> 1-kanal, 1602 -> 16-kanal
            return max(1, n)
    return 1


# ---------- ONVIF: darhol keyframe so'rash ----------

_MEDIA_NS = "http://www.onvif.org/ver10/media/wsdl"
_SERVICE_PATHS = ("/onvif/media_service", "/onvif/Media", "/onvif/device_service")

_service_cache: dict[str, str] = {}        # ip -> javob bergan service yo'li
# ip -> (profil tokenlari, kesh muddati). Bo'sh ro'yxat "qo'llamaydi" degani,
# lekin muddatli — ONVIF keyinroq yoqilsa, qayta ishga tushirish shart emas.
_profile_cache: dict[str, tuple[list[str], float]] = {}
_NO_ONVIF_TTL = 600.0
_last_keyframe: dict[tuple, float] = {}    # (ip, path) -> oxirgi so'rov vaqti
_lock = threading.Lock()


def _security_header(username: str, password: str) -> str:
    """WS-UsernameToken (PasswordDigest) — ONVIF'ning standart autentifikatsiyasi."""
    if not username:
        return ""
    nonce = os.urandom(16)
    created = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    digest = base64.b64encode(
        hashlib.sha1(nonce + created.encode() + (password or "").encode()).digest()
    ).decode()
    return (
        '<wsse:Security s:mustUnderstand="1" '
        'xmlns:wsse="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-wssecurity-secext-1.0.xsd" '
        'xmlns:wsu="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-wssecurity-utility-1.0.xsd">'
        "<wsse:UsernameToken>"
        f"<wsse:Username>{escape(username)}</wsse:Username>"
        '<wsse:Password Type="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-username-token-profile-1.0#PasswordDigest">'
        f"{digest}</wsse:Password>"
        '<wsse:Nonce EncodingType="http://docs.oasis-open.org/wss/2004/01/'
        'oasis-200401-wss-soap-message-security-1.0#Base64Binary">'
        f"{base64.b64encode(nonce).decode()}</wsse:Nonce>"
        f"<wsu:Created>{created}</wsu:Created>"
        "</wsse:UsernameToken></wsse:Security>"
    )


def _soap(url: str, header: str, body: str) -> str:
    envelope = (
        '<s:Envelope xmlns:s="http://www.w3.org/2003/05/soap-envelope">'
        f"<s:Header>{header}</s:Header><s:Body>{body}</s:Body></s:Envelope>"
    )
    req = urllib.request.Request(url, data=envelope.encode(), method="POST")
    req.add_header("Content-Type", "application/soap+xml; charset=utf-8")
    with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
        return res.read().decode("utf-8", "replace")


def _media_profiles(ip: str, username: str, password: str) -> list[str]:
    """Kameraning ONVIF profil tokenlari — bir marta so'rab keshlanadi."""
    cached = _profile_cache.get(ip)
    if cached is not None and (cached[0] or cached[1] > time.monotonic()):
        return cached[0]

    body = f'<trt:GetProfiles xmlns:trt="{_MEDIA_NS}"/>'
    known = _service_cache.get(ip)
    paths = (known,) if known else _SERVICE_PATHS
    for path in paths:
        url = f"http://{ip}:{HTTP_PORT}{path}"
        try:
            resp = _soap(url, _security_header(username, password), body)
        except (urllib.error.URLError, OSError):
            continue
        tokens = re.findall(r'Profiles[^>]*token="([^"]+)"', resp)
        if tokens:
            with _lock:
                _profile_cache[ip] = (tokens, float("inf"))
                _service_cache[ip] = path
            return tokens
    with _lock:
        _profile_cache[ip] = ([], time.monotonic() + _NO_ONVIF_TTL)
    return []


def _onvif_keyframe(ip: str, username: str, password: str,
                    rtsp_path: str) -> bool:
    tokens = _media_profiles(ip, username, password)
    if not tokens:
        return False

    # Profillar odatda kanal tartibida keladi: 1-main, 1-sub, 2-main, ...
    index = (channel_from_path(rtsp_path) - 1) * 2
    token = tokens[index] if index < len(tokens) else tokens[0]

    body = (
        f'<trt:SetSynchronizationPoint xmlns:trt="{_MEDIA_NS}">'
        f"<trt:ProfileToken>{escape(token)}</trt:ProfileToken>"
        "</trt:SetSynchronizationPoint>"
    )
    url = f"http://{ip}:{HTTP_PORT}{_service_cache[ip]}"
    try:
        _soap(url, _security_header(username, password), body)
        return True
    except (urllib.error.URLError, OSError):
        return False


def _auth_opener(url: str, username: str, password: str):
    handlers = []
    if username:
        mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()
        mgr.add_password(None, url, username, password)
        # Kameralarning aksariyati Digest, eskilari Basic ishlatadi.
        handlers = [urllib.request.HTTPDigestAuthHandler(mgr),
                    urllib.request.HTTPBasicAuthHandler(mgr)]
    return urllib.request.build_opener(*handlers)


def _isapi_keyframe(ip: str, username: str, password: str,
                    channel: int) -> bool:
    """Hikvision'ning o'z usuli — ONVIF o'chirilgan bo'lsa ham ishlaydi."""
    url = (f"http://{ip}:{HTTP_PORT}/ISAPI/Streaming/channels/"
           f"{channel}01/requestKeyFrame")
    req = urllib.request.Request(url, data=b"", method="PUT")
    try:
        with _auth_opener(url, username, password).open(req, timeout=TIMEOUT):
            return True
    except (urllib.error.URLError, OSError):
        return False


def request_keyframe(ip: str, username: str, password: str,
                     rtsp_path: str = "", vendor: str = "") -> bool:
    """Kameradan darhol keyframe (I-frame) yuborishni so'raydi."""
    if not ip:
        return False
    key = (ip, rtsp_path or "")
    now = time.monotonic()
    with _lock:
        if now - _last_keyframe.get(key, 0.0) < 2.0:   # bosimdan saqlanish
            return False
        _last_keyframe[key] = now

    if _onvif_keyframe(ip, username, password, rtsp_path):
        return True
    if vendor == "hikvision":
        return _isapi_keyframe(ip, username, password,
                               channel_from_path(rtsp_path))
    return False


def request_keyframe_async(ip: str, username: str, password: str,
                           rtsp_path: str = "", vendor: str = "") -> None:
    """Keyframe so'rovi fonda ketadi — oqim manzili javobini kechiktirmaydi."""
    threading.Thread(
        target=request_keyframe,
        args=(ip, username, password, rtsp_path, vendor),
        daemon=True,
    ).start()


# ---------- JPEG surat (snapshot) ----------

SNAPSHOT_TTL = 8.0                          # soniya — shu orada bitta surat yetadi
_SNAP_CACHE_MAX = 500                       # surat ~200 KB: chegara ≈ 100 MB

_snap_cache: dict[int, tuple[float, bytes | None]] = {}
_snap_url: dict[int, str] = {}              # camera_id -> ishlagan URL


def _snap_store(camera_id: int, expires: float, data: bytes | None) -> None:
    """Keshga yozadi va hajmni chegarada ushlaydi.

    5000 kamerada chegarasiz kesh gigabaytga o'sib ketardi. Avval muddati
    o'tganlar, yetmasa eng eskilari chiqariladi.
    """
    _snap_cache[camera_id] = (expires, data)
    if len(_snap_cache) <= _SNAP_CACHE_MAX:
        return
    now = time.monotonic()
    for key, (ttl, _) in list(_snap_cache.items()):
        if ttl <= now:
            _snap_cache.pop(key, None)
    while len(_snap_cache) > _SNAP_CACHE_MAX:
        oldest = min(_snap_cache, key=lambda k: _snap_cache[k][0])
        _snap_cache.pop(oldest, None)


def _snapshot_candidates(vendor: str, ip: str, username: str,
                         password: str, channel: int) -> list[str]:
    """Ishlab chiqaruvchiga qarab surat manzillari — birinchi ishlagani keshlanadi."""
    base = f"http://{ip}:{HTTP_PORT}"
    hik = f"{base}/ISAPI/Streaming/channels/{channel}01/picture"
    dahua = f"{base}/cgi-bin/snapshot.cgi?channel={channel}"
    urls: list[str] = []
    if vendor == "hikvision":
        urls.append(hik)
    elif vendor in ("dahua", "amcrest"):
        urls.append(dahua)
    elif vendor == "axis":
        urls.append(f"{base}/axis-cgi/jpg/image.cgi?camera={channel}")
    elif vendor == "reolink":
        urls.append(f"{base}/cgi-bin/api.cgi?cmd=Snap&channel={channel - 1}"
                    f"&user={urllib.parse.quote(username, safe='')}"
                    f"&password={urllib.parse.quote(password, safe='')}")
    elif vendor == "uniview":
        urls.append(f"{base}/images/snapshot.jpg")
    # Noma'lum/ishlamagan holatlar uchun keng tarqalgan yo'llar.
    for candidate in (hik, dahua, f"{base}/snapshot.jpg", f"{base}/image.jpg"):
        if candidate not in urls:
            urls.append(candidate)
    return urls


def _fetch_image(url: str, username: str, password: str) -> bytes | None:
    with _auth_opener(url, username, password).open(url, timeout=TIMEOUT) as res:
        data = res.read(2_000_000)
    return data if data[:2] == b"\xff\xd8" else None    # JPEG belgisi


_FFMPEG_SENTINEL = "__ffmpeg__"


@lru_cache(maxsize=1)
def _ffmpeg_exe() -> str:
    return shutil.which("ffmpeg") or ""


def _ffmpeg_snapshot(slug: str) -> bytes | None:
    """Zaxira yo'l: MediaMTX'dagi oqimdan bitta kadr olinadi.

    HTTP-snapshot bermaydigan kameralar uchun — MediaMTX kamerani baribir
    talab bo'yicha tortadi, biz undan lokal ulanish orqali kadr olamiz
    (kameraga qo'shimcha ulanish ochilmaydi).
    """
    exe = _ffmpeg_exe()
    if not exe or not slug:
        return None
    url = f"rtsp://127.0.0.1:{os.environ.get('MEDIAMTX_RTSP_PORT', '8554')}/{slug}"
    try:
        out = subprocess.run(
            [exe, "-hide_banner", "-loglevel", "error",
             "-rtsp_transport", "tcp", "-i", url,
             "-frames:v", "1", "-q:v", "4", "-f", "image2", "-"],
            capture_output=True, timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    data = out.stdout
    return data if data[:2] == b"\xff\xd8" else None


def snapshot(camera_id: int, ip: str, username: str, password: str,
             vendor: str, rtsp_path: str, slug: str = "") -> bytes | None:
    """Kameraning JPEG suratini qaytaradi (qisqa muddat keshlab).

    Avval kameraning HTTP-snapshot manzillari sinaladi (eng tez yo'l),
    ular ishlamasa — MediaMTX'dagi oqimdan kadr olinadi (har qanday
    kamera uchun ishlaydi, lekin sekinroq).
    """
    if not ip:
        return None
    now = time.monotonic()
    cached = _snap_cache.get(camera_id)
    if cached and cached[0] > now:
        return cached[1]

    channel = channel_from_path(rtsp_path)
    known = _snap_url.get(camera_id)
    if known != _FFMPEG_SENTINEL:
        candidates = ([known] if known else
                      _snapshot_candidates(vendor, ip, username, password, channel))
        for url in candidates:
            try:
                data = _fetch_image(url, username, password)
            except (urllib.error.URLError, OSError, ValueError):
                continue
            if data:
                _snap_store(camera_id, now + SNAPSHOT_TTL, data)
                _snap_url[camera_id] = url
                return data

    data = _ffmpeg_snapshot(slug)
    if data:
        _snap_store(camera_id, now + SNAPSHOT_TTL, data)
        _snap_url[camera_id] = _FFMPEG_SENTINEL   # keyingi safar to'g'ri shu yo'l
        return data

    # Muvaffaqiyatsizlik ham keshlanadi — o'chiq kamerani qayta-qayta so'ramaslik uchun.
    _snap_store(camera_id, now + SNAPSHOT_TTL, None)
    _snap_url.pop(camera_id, None)
    return None

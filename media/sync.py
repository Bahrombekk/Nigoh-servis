"""Nigoh — MediaMTX bilan bog'lash (media paketi).

Backend MediaMTX bilan faqat shu modul orqali gaplashadi: konfiguratsiya
yaratish (`write_config`), jonli API (`ensure_path`, `push_to_api`) va
FFmpeg buyruqlari shu yerda.

Konfiguratsiya kameralar soniga bog'liq emas. `mediamtx.yml` ichida
kameralar ro'yxati ham, parollar ham yozilmaydi — bitta shablon yo'l
bor, u chaqirilganda `stream_launcher.py` bazadan kerakli kamerani topadi.

Nima uchun shunday:

  * 1000 ta kamera bo'lsa ham fayl o'zgarmaydi va MediaMTX'ni qayta
    ishga tushirish shart emas — yangi kamera qo'shilishi bilan ishlaydi.
  * Parollar faqat shifrlangan holda bazada qoladi; konfiguratsiya
    fayliga ochiq holda tushmaydi.
  * Kamera faqat kimdir ko'rayotganda ulanadi, ya'ni resurs kameralar
    soniga emas, tomoshabinlar soniga qarab sarflanadi.

"Doim tayyor" deb belgilangan kameralargina alohida yoziladi — ular
bir zumda ochiladi, lekin doimo resurs egallaydi.

Kodek haqida: ko'p kamera H.265 (HEVC) beradi, brauzerlar buni o'qiy
olmaydi. Bunday kameralar FFmpeg orqali H.264 ga o'giriladi; NVIDIA
karta bo'lsa butun jarayon GPU'da ketadi.
"""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

from core.db import DATA_DIR
from core.rtsp_probe import build_rtsp_url

import yaml

# Loyiha ildizi — bu fayl media/ ichida turadi. mediamtx.yml ma'lumotlar
# katalogida (standart — ildiz; konteynerda NIGOH_DATA volume).
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = DATA_DIR / "mediamtx.yml"
API_BASE = os.environ.get("MEDIAMTX_API", "http://127.0.0.1:9997")
API_TIMEOUT = 4.0

RTSP_PORT = int(os.environ.get("MEDIAMTX_RTSP_PORT", "8554"))
HLS_PORT = int(os.environ.get("HLS_PORT", "8888"))
WEBRTC_PORT = int(os.environ.get("WEBRTC_PORT", "8889"))

# MediaMTX har bir ulanishda backend'dan ruxsat so'raydi. MediaMTX boshqa
# mashinada bo'lsa, STREAM_AUTH_URL orqali backend'ning to'liq manzilini
# bering (u mashinadan yetib boradigan qilib).
APP_PORT = int(os.environ.get("PORT", "8010"))
STREAM_AUTH_URL = os.environ.get(
    "STREAM_AUTH_URL", f"http://127.0.0.1:{APP_PORT}/api/auth/stream")

HEADER = """# Nigoh tomonidan avtomatik yaratilgan — qo'lda tahrirlamang.
# Kameralarni saytdagi super-admin panelidan boshqaring; bu fayl
# "MediaMTX" oynasidagi tugma bosilganda qayta yoziladi.
"""


# ---------- FFmpeg ----------

@lru_cache(maxsize=1)
def ffmpeg_path() -> str:
    return shutil.which("ffmpeg") or ""


@lru_cache(maxsize=1)
def has_nvenc() -> bool:
    """NVIDIA GPU orqali H.264 kodlash mumkinmi."""
    exe = ffmpeg_path()
    if not exe:
        return False
    try:
        out = subprocess.run([exe, "-hide_banner", "-encoders"],
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return False
    return "h264_nvenc" in out.stdout


_INPUT = ["-hide_banner", "-loglevel", "warning",
          "-fflags", "nobuffer", "-flags", "low_delay",
          # FFmpeg standart holda oqimni 5 soniya "o'rganadi" — kamerada
          # bitta video yo'l bo'lgani uchun bunga hojat yo'q, shu bilan
          # birinchi ochilish bir necha soniyaga qisqaradi.
          "-analyzeduration", "1000000", "-probesize", "1000000",
          "-rtsp_transport", "tcp"]

# WebRTC uchun paketlar kichik bo'lsin — MediaMTX ularni qayta bo'lmasin.
_OUTPUT = ["-an", "-pkt_size", "1200", "-f", "rtsp", "-rtsp_transport", "tcp"]


def relay_args(src_url: str, dst_url: str) -> list[str]:
    """Kamera H.264 bergan holat: video umuman ochilmaydi.

    Paketlar borligicha uzatiladi — na dekodlash, na kodlash bor.
    Sarf: bir necha foiz protsessor va ~30 MB xotira.
    """
    return _INPUT + ["-i", src_url, "-c", "copy"] + _OUTPUT + [dst_url]


def transcode_args(src_url: str, dst_url: str, gpu: bool = True,
                   bitrate: str = "3M") -> list[str]:
    """Kamera H.265 bergan holat: dekodlash va qayta kodlash.

    H.265 va H.264 — bir-biriga o'xshamaydigan siqish usullari, shuning
    uchun oraliq qadamsiz o'girib bo'lmaydi: tasvirni ochib, qaytadan
    siqish shart. Buni yo'qotishning yagona yo'li — kamerani H.264 ga
    o'tkazish, shunda yuqoridagi `relay_args` ishlaydi.
    """
    if gpu:
        # Dekodlash ham, kodlash ham GPU'da — nusxalashsiz.
        video = [
            "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
            "-i", src_url,
            "-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ull",
            "-rc", "cbr", "-b:v", bitrate,
        ]
    else:
        video = [
            "-i", src_url,
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-b:v", bitrate,
        ]
    # Qisqa GOP — segment tezroq tayyor bo'ladi.
    return _INPUT + video + ["-g", "30", "-bf", "0"] + _OUTPUT + [dst_url]


# ---------- issiq to'plam (warm set) ----------
#
# Sub oqim ~0,5 Mbit/s va ~15 MB xotira — tayyor tutish deyarli tekin,
# ochilish esa sourceOnDemand kutishisiz < 1 soniya bo'ladi. Oqim
# so'ralganda sub yo'l 10 daqiqaga "issiq" belgilanadi: sourceOnDemand
# o'chiriladi (doim ulangan). Muddati o'tsa reconciler navbatdagi tsiklda
# farqni ko'rib sovutadi. Asosiy oqim uchun bu qimmat — faqat sub.

WARM_TTL = 600.0       # soniya — oxirgi so'rovdan keyin shuncha issiq turadi
WARM_LIMIT = 256       # bir vaqtda issiq yo'llar chegarasi

_warm: dict[str, float] = {}          # sub-slug -> muddati (monotonic)
_warm_lock = threading.Lock()


def mark_warm(slug: str) -> bool:
    """Sub yo'lni issiq qiladi (muddatni yangilaydi). Chegara to'lsa False."""
    now = time.monotonic()
    with _warm_lock:
        for key in [k for k, t in _warm.items() if t <= now]:
            _warm.pop(key, None)
        if slug not in _warm and len(_warm) >= WARM_LIMIT:
            return False
        _warm[slug] = now + WARM_TTL
        return True


def is_warm(slug: str) -> bool:
    with _warm_lock:
        return _warm.get(slug, 0.0) > time.monotonic()


def warm_count() -> int:
    now = time.monotonic()
    with _warm_lock:
        return sum(1 for t in _warm.values() if t > now)


# ---------- yo'llar ----------

TRANSCODE_SUFFIX = "_h264"
SUB_SUFFIX = "_sub"


def sub_variant(cam: dict) -> dict | None:
    """Kameraning past sifatli ikkinchi oqimi (`<slug>_sub` yo'li).

    Video devor 4×4 setkada 16 ta to'liq oqim tortmasin — sub-stream
    tarmoq va dekodlash yukini ~10 barobar kamaytiradi. Sub odatda H.264
    bo'ladi, shuning uchun o'girish ham kerak emas.
    """
    if not cam.get("sub_path"):
        return None
    return {**cam, "slug": cam["slug"] + SUB_SUFFIX,
            "rtsp_path": cam["sub_path"], "always_on": False}


def _launcher(slug_expr: str) -> str:
    """`stream_launcher.py` ni chaqiruvchi buyruq."""
    python = sys.executable or "python"
    script = BASE_DIR / "stream_launcher.py"
    return f'"{python}" "{script}" {slug_expr}'


def source_path(cam: dict) -> dict:
    """Kamerani MediaMTX o'zi tortadigan yo'l.

    FFmpeg ishlatilmaydi: MediaMTX RTSP'ni to'g'ridan-to'g'ri oladi. Bu ham
    tezroq (jarayon ishga tushirilmaydi), ham yengilroq (~220 MB o'rniga
    bir necha MB), ham ishonchliroq — FFmpeg nusxalashda B-kadrli H.264
    oqimni buzib yuborardi.
    """
    conf = {
        "source": build_rtsp_url(
            cam["ip"], cam["port"], cam.get("rtsp_path") or "/",
            cam.get("username") or "", cam.get("password") or "",
        ),
        # UDP'da paketlar yo'qoladi va tasvir buziladi — TCP majburiy.
        "rtspTransport": "tcp",
        # Issiq sub yo'llar ham "doim tayyor" hisoblanadi.
        "sourceOnDemand": not (cam.get("always_on") or is_warm(cam["slug"])),
    }
    if conf["sourceOnDemand"]:
        conf["sourceOnDemandStartTimeout"] = "20s"
        # MediaMTX davomiyliklarni normallashtirib saqlaydi ("60s" -> "1m0s").
        # Taqqoslash (ensure_path/push_to_api) aynan mos kelishi uchun
        # qiymatlar uning o'z shaklida yoziladi — aks holda har safar
        # keraksiz PATCH ketadi.
        conf["sourceOnDemandCloseAfter"] = "1m0s"
    return conf


def camera_paths(cameras: list[dict]) -> dict:
    """MediaMTX `paths` bo'limi.

    Kameralar bu yerga yozilmaydi — ular ishlab turgan MediaMTX'ga API
    orqali qo'shiladi (`ensure_path`). Faylda faqat o'girish uchun shablon
    qoladi, shuning uchun 1000 kamerada ham hajmi o'zgarmaydi va parollar
    diskka tushmaydi.
    """
    return {
        f"~^[a-z0-9_]+{TRANSCODE_SUFFIX}$": {
            "runOnDemand": _launcher("$MTX_PATH"),
            "runOnDemandRestart": True,
            "runOnDemandStartTimeout": "20s",
            "runOnDemandCloseAfter": "1m0s",   # MediaMTX normallashtirgan shakl
        }
    }


def build_config(cameras: list[dict], auth_url: str | None = None,
                 node: dict | None = None) -> str:
    """To'liq mediamtx.yml matnini qaytaradi.

    `node` berilsa — o'sha tugun uchun konfiguratsiya: portlar tugundan
    olinadi, API hamma interfeysda tinglaydi (markaziy backend yo'llarni
    shu API orqali boshqaradi — portni faqat backend'ga oching) va
    o'girish shabloni yozilmaydi (launcher u mashinada yo'q).
    """
    node = node or {}
    remote = bool(node) and not is_local_api(node.get("api_base"))
    rtsp_port = int(node.get("rtsp_port") or RTSP_PORT)
    hls_port = int(node.get("hls_port") or HLS_PORT)
    webrtc_port = int(node.get("webrtc_port") or WEBRTC_PORT)
    config = {
        "logLevel": "info",
        "api": True,
        "apiAddress": ":9997" if remote else "127.0.0.1:9997",

        # Prometheus metrikalari (oqimlar, tomoshabinlar, baytlar) —
        # keyinchalik Grafana ulash uchun tayyor turadi.
        "metrics": True,
        "metricsAddress": ":9998" if remote else "127.0.0.1:9998",

        # Kirish nazorati: har bir o'qish so'rovini backend tekshiradi —
        # saytdan berilgan chiptasiz oqim ochilmaydi. Backend ishlamayotgan
        # bo'lsa MediaMTX hamma so'rovni rad etadi (yopiq holatda xavfsiz).
        # API lokal portda va shusiz ham faqat 127.0.0.1 dan ochiq.
        "authMethod": "http",
        "authHTTPAddress": auth_url or STREAM_AUTH_URL,
        "authHTTPExclude": [
            {"action": "api"}, {"action": "metrics"}, {"action": "pprof"},
        ],

        "rtsp": True,
        "rtspAddress": f":{rtsp_port}",
        "rtspTransports": ["tcp"],

        # WebRTC — asosiy yo'l, eng tez ochiladi.
        "webrtc": True,
        "webrtcAddress": f":{webrtc_port}",
        "webrtcAllowOrigins": ["*"],
        "webrtcLocalUDPAddress": ":8189",
        # Nginx (127.0.0.1) orqali kelgan so'rovlarda haqiqiy tomoshabin
        # IP'si X-Forwarded-For sarlavhasidan olinadi — auth va HLS
        # sessiyalari to'g'ri IP bilan ishlaydi.
        "webrtcTrustedProxies": ["127.0.0.1"],

        # HLS — WebRTC ishlamagan brauzerlar uchun zaxira.
        "hls": True,
        "hlsAddress": f":{hls_port}",
        "hlsVariant": "lowLatency",
        # Doimiy remux qilinsa xom H.265 yo'llari ham bekorga HLS'ga o'giriladi;
        # asosiy yo'l WebRTC bo'lgani uchun bunga hojat yo'q.
        "hlsAlwaysRemux": False,
        "hlsSegmentCount": 7,
        "hlsSegmentDuration": "1s",
        "hlsPartDuration": "200ms",
        "hlsAllowOrigins": ["*"],
        "hlsTrustedProxies": ["127.0.0.1"],

        "rtmp": False,
        "srt": False,

        "paths": {} if remote else (camera_paths(cameras) or {}),
    }
    body = yaml.safe_dump(config, allow_unicode=True, sort_keys=False,
                          default_flow_style=False, width=10000)
    return HEADER + "\n" + body


def write_config(cameras: list[dict]) -> int:
    """mediamtx.yml faylini qayta yozadi, tayyor kameralar sonini qaytaradi."""
    CONFIG_PATH.write_text(build_config(cameras), encoding="utf-8")
    return sum(1 for c in cameras if c.get("ip") and c.get("enabled"))


# ---------- ishlab turgan MediaMTX bilan aloqa ----------
#
# Barcha funksiyalar ixtiyoriy `api_base` oladi — ko'p tugunli rejimda
# har bir tugunning o'z API manzili bo'ladi (nodes jadvali). Berilmasa
# lokal (asosiy) tugun ishlatiladi.

def is_local_api(api_base: str | None = None) -> bool:
    """API manzili shu mashinadami — jarayonni faqat lokalda boshqaramiz."""
    host = (api_base or API_BASE).split("//")[-1].split(":")[0]
    return host in ("127.0.0.1", "localhost", "::1")


def _api(method: str, path: str, payload: dict | None = None,
         api_base: str | None = None):
    url = f"{(api_base or API_BASE).rstrip('/')}{path}"
    data = json.dumps(payload).encode() if payload is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(req, timeout=API_TIMEOUT) as res:
        raw = res.read()
    return json.loads(raw) if raw else None


def api_available(api_base: str | None = None) -> bool:
    try:
        _api("GET", "/v3/config/global/get", api_base=api_base)
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False


def ensure_path(cam: dict, api_base: str | None = None) -> bool:
    """Kamera yo'li MediaMTX'da borligiga ishonch hosil qiladi.

    Ko'rish so'ralganda chaqiriladi. Yo'l yo'q bo'lsa qo'shiladi, borligi
    boshqacha bo'lsa yangilanadi. Shu sababli MediaMTX qayta ishga tushsa
    ham hech narsani qo'lda tiklash kerak emas.
    """
    if not cam.get("ip"):
        return False
    slug, wanted = cam["slug"], source_path(cam)
    try:
        current = _api("GET", f"/v3/config/paths/get/{slug}", api_base=api_base)
    except urllib.error.HTTPError as exc:
        if exc.code != 404:
            return False
        current = None
    except (urllib.error.URLError, OSError, ValueError):
        return False

    try:
        if current is None:
            _api("POST", f"/v3/config/paths/add/{slug}", wanted, api_base=api_base)
        elif any(current.get(k) != v for k, v in wanted.items()):
            _api("PATCH", f"/v3/config/paths/patch/{slug}", wanted, api_base=api_base)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return False


def ensure_transcode_path(cam: dict) -> bool:
    """"Tez ochilsin" kameralari uchun o'girilgan oqim doim tayyor tursin.

    Shablon yo'l o'girishni faqat so'ralganda boshlaydi; bu yerda esa uni
    doimiy ishlatib qo'yamiz, aks holda "tez" degani birinchi ochilishda
    ishlamaydi.
    """
    if not (cam.get("transcode") and cam.get("always_on") and cam.get("enabled")):
        return False
    name = cam["slug"] + TRANSCODE_SUFFIX
    wanted = {"runOnInit": _launcher(name), "runOnInitRestart": True}
    try:
        try:
            current = _api("GET", f"/v3/config/paths/get/{name}")
        except urllib.error.HTTPError as exc:
            if exc.code != 404:
                return False
            current = None
        if current is None:
            _api("POST", f"/v3/config/paths/add/{name}", wanted)
        elif current.get("runOnInit") != wanted["runOnInit"]:
            _api("PATCH", f"/v3/config/paths/patch/{name}", wanted)
        return True
    except (urllib.error.URLError, urllib.error.HTTPError, OSError, ValueError):
        return False


def desired_paths(cameras: list[dict], with_transcode: bool = True) -> dict:
    """MediaMTX'da (API orqali) turishi kerak bo'lgan to'liq holat.

    Uch qatlam: o'girish shabloni, har bir yoqilgan kameraning manba yo'li
    va "doim tayyor" o'girish yo'llari. `push_to_api` MediaMTX'ni aynan shu
    ro'yxatga keltiradi — ortiqchasi o'chadi, kamisi qo'shiladi.

    `with_transcode=False` — uzoq tugunlar uchun: o'girish yo'llari
    `stream_launcher.py` ni chaqiradi, u esa faqat backend turgan
    mashinada bor. Uzoq tugun kameralarini H.264 da tuting.
    """
    wanted = dict(camera_paths(cameras)) if with_transcode else {}
    for cam in cameras:
        if not (cam.get("enabled") and cam.get("ip")):
            continue
        wanted[cam["slug"]] = source_path(cam)
        sub = sub_variant(cam)
        if sub:
            wanted[sub["slug"]] = source_path(sub)
        if with_transcode and cam.get("transcode") and cam.get("always_on"):
            name = cam["slug"] + TRANSCODE_SUFFIX
            wanted[name] = {"runOnInit": _launcher(name), "runOnInitRestart": True}
    return wanted


def _paged_list(endpoint: str, api_base: str | None = None) -> dict[str, dict] | None:
    """MediaMTX ro'yxatini sahifalab, to'liq o'qiydi.

    Bitta so'rov 1000 tagacha qaytaradi; 5000 kamerada qolgani ko'rinmay
    qolardi, shuning uchun `pageCount` tugaguncha o'qiladi.
    """
    paths: dict[str, dict] = {}
    page = 0
    while True:
        try:
            chunk = _api("GET", f"{endpoint}?itemsPerPage=500&page={page}",
                         api_base=api_base) or {}
        except (urllib.error.URLError, OSError, ValueError):
            return None
        for item in chunk.get("items", []):
            paths[item["name"]] = item
        page += 1
        if page >= int(chunk.get("pageCount") or 1):
            return paths


def _list_all_paths(api_base: str | None = None) -> dict[str, dict] | None:
    """API orqali sozlangan barcha yo'l konfiguratsiyalari."""
    return _paged_list("/v3/config/paths/list", api_base)


def list_active_paths(api_base: str | None = None) -> dict[str, dict] | None:
    """Ayni damda faol (runtime) yo'llar: ready, bytesReceived, o'quvchilar.

    Konfiguratsiyadan farqi — bu ro'yxatda faqat hozir ishlab turgan
    oqimlar bo'ladi. Reconciler shundan oqim muzlaganini aniqlaydi:
    kamera portga javob bersa ham bayt hisobi joyidan qo'zg'almasa,
    tasvir kelmayapti degani.
    """
    return _paged_list("/v3/paths/list", api_base)


def node_runtime(api_base: str | None = None) -> dict | None:
    """Tugunning ish vaqti ko'rsatkichlari — bir qarashda salomatlik.

    MediaMTX'ning faol yo'llar ro'yxatidan yig'iladi: nechta oqim sozlangan,
    nechtasi tayyor (kameradan tasvir kelyapti), jami nechta tomoshabin va
    qancha trafik o'tgan. API javob bermasa None.
    """
    paths = list_active_paths(api_base)
    if paths is None:
        return None
    ready = sum(1 for p in paths.values() if p.get("ready"))
    return {
        "paths": len(paths),
        "ready": ready,
        "readers": sum(len(p.get("readers") or []) for p in paths.values()),
        "bytes_received": sum(int(p.get("bytesReceived") or 0)
                              for p in paths.values()),
        "bytes_sent": sum(int(p.get("bytesSent") or 0) for p in paths.values()),
    }


def push_to_api(cameras: list[dict], api_base: str | None = None,
                with_transcode: bool | None = None) -> dict:
    """Ishlab turgan MediaMTX'ni kerakli holatga keltiradi (qayta ishga
    tushirmasdan): yo'q yo'llar qo'shiladi, o'zgarganlari yangilanadi,
    ortiqchalari o'chiriladi. O'zgarmaganlarga tegilmaydi, amallar parallel
    yuboriladi — 5000 kamerada ham soniyalar ichida tugaydi.
    """
    if with_transcode is None:
        with_transcode = is_local_api(api_base)   # o'girish faqat lokal tugunda
    wanted = desired_paths(cameras, with_transcode)
    existing = _list_all_paths(api_base)
    if existing is None:
        return {"ok": False, "added": 0, "updated": 0, "removed": 0,
                "message": "MediaMTX ishlamayapti — fayl yangilandi, "
                           "MediaMTX'ni ishga tushiring"}

    ops: list[tuple[str, str, dict | None]] = []
    for name, conf in wanted.items():
        current = existing.get(name)
        if current is None:
            ops.append(("POST", f"/v3/config/paths/add/{name}", conf))
        elif any(current.get(k) != v for k, v in conf.items()):
            ops.append(("PATCH", f"/v3/config/paths/patch/{name}", conf))
    for name in existing:
        if name not in wanted:
            ops.append(("DELETE", f"/v3/config/paths/delete/{name}", None))

    def _run(op: tuple[str, str, dict | None]):
        method, path, payload = op
        try:
            _api(method, path, payload, api_base=api_base)
            return method, None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return method, f"{path.rsplit('/', 1)[-1]}: {exc}"

    added = updated = removed = 0
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        for method, error in pool.map(_run, ops):
            if error is not None:
                if method != "DELETE":     # o'chirishdagi xato jiddiy emas
                    errors.append(error)
            elif method == "POST":
                added += 1
            elif method == "PATCH":
                updated += 1
            else:
                removed += 1

    if errors:
        return {"ok": False, "added": added, "updated": updated, "removed": removed,
                "message": "Ba'zi yo'llar yuborilmadi: " + "; ".join(errors[:2])}
    return {"ok": True, "added": added, "updated": updated, "removed": removed,
            "message": f"MediaMTX yangilandi (+{added} / ~{updated} / -{removed})"}

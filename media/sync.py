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
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from functools import lru_cache
from pathlib import Path

import yaml

from core.db import DATA_DIR
from core.rtsp_probe import build_rtsp_url

# Loyiha ildizi — bu fayl media/ ichida turadi. mediamtx.yml ma'lumotlar
# katalogida (standart — ildiz; konteynerda NIGOH_DATA volume).
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = DATA_DIR / "mediamtx.yml"
API_BASE = os.environ.get("MEDIAMTX_API", "http://127.0.0.1:9997")
API_TIMEOUT = 4.0

RTSP_PORT = int(os.environ.get("MEDIAMTX_RTSP_PORT", "8554"))
HLS_PORT = int(os.environ.get("HLS_PORT", "8888"))
WEBRTC_PORT = int(os.environ.get("WEBRTC_PORT", "8889"))

# WebRTC media portlari. UDP — asosiy (eng samarali). TCP esa MediaMTX'da
# standart holda O'CHIQ, natijada UDP yopiq tarmoqda (korporativ firewall,
# ba'zi mobil operatorlar) brauzer jimgina HLS'ga tushardi — ya'ni eng
# sekin yo'lga. ICE ustuvorligi baribir avval UDP'ni sinaydi, TCP faqat
# zaxira bo'lib qoladi. Firewall'da 8189 ni ikkala protokol uchun oching.
# WEBRTC_TCP_PORT=0 — butunlay o'chirish.
WEBRTC_UDP_PORT = int(os.environ.get("WEBRTC_UDP_PORT", "8189"))
WEBRTC_TCP_PORT = int(os.environ.get("WEBRTC_TCP_PORT", "8189"))

def _webrtc_hosts() -> list[str]:
    """Brauzerga WebRTC uchun e'lon qilinadigan manzillar.

    Bo'sh qolsa MediaMTX faqat mashinaning O'Z interfeys manzillarini
    beradi (127.0.0.1, ichki LAN IP, docker0) — internetdagi brauzer
    ularning birortasiga yeta olmaydi, ICE ulanmaydi va tomoshabin
    jimgina HLS'ga tushadi. Tashqaridan bu "WebRTC ishlamayapti" bo'lib
    ko'rinadi, holbuki sozlama yetishmayapti.

    Shu sababli MEDIA_BASE ham manba hisoblanadi: agar operator
    `MEDIA_BASE=https://kamera.example.uz/media` deb yozgan bo'lsa,
    brauzer o'sha domenga yetadi degani — ikkinchi o'zgaruvchini
    talab qilishning ma'nosi yo'q.

    Tartib: WEBRTC_HOSTS -> MEDIA_HOST -> MEDIA_BASE hosti.

    Har bir qiymat HOST'ga keltiriladi. ICE nomzodiga faqat host yoki IP
    yozilishi mumkin: sxema, yo'l yoki port bilan berilgan qiymat
    MediaMTX'ga yaroqsiz nomzod bo'lib tushadi va ulanish jimgina
    qurilmay qoladi. Amalda `MEDIA_HOST` ga to'liq URL yozib qo'yilgani
    uchraydi, shuning uchun tozalash shu yerda qilinadi.
    """
    raw = os.environ.get("WEBRTC_HOSTS") or os.environ.get("MEDIA_HOST") or ""
    if not raw.strip():
        raw = os.environ.get("MEDIA_BASE", "").strip()
    return [h for h in (_host_only(v) for v in raw.split(",")) if h]


def _host_only(value: str) -> str:
    """"https://kamera.example.uz/media" -> "kamera.example.uz".

    Sxemasiz yozilgani ham to'g'ri ishlashi kerak ("kamera.example.uz/media"),
    shuning uchun sxema bo'lmasa vaqtincha qo'shib qo'yiladi — usiz
    urlsplit butun satrni yo'l deb qabul qiladi.
    """
    value = value.strip().rstrip("/")
    if not value:
        return ""
    if "://" not in value:
        # Port yoki yo'l bo'lmasa parse qilishning hojati yo'q.
        if "/" not in value and ":" not in value:
            return value
        value = "//" + value
    try:
        return urllib.parse.urlsplit(value).hostname or ""
    except ValueError:
        return ""


WEBRTC_HOSTS = _webrtc_hosts()

# Chiqish navbati. MediaMTX standarti — 512; bitta oqimni ko'p tomoshabin
# ko'rganda u to'lib ketadi va server "reader is too slow" deb paketlarni
# tashlaydi (tasvir uzuq-yuluq bo'ladi). Ikkining darajasi bo'lishi shart.
WRITE_QUEUE_SIZE = int(os.environ.get("MEDIAMTX_WRITE_QUEUE", "1024"))

# Bitta sinxronlash tsikliga ajratiladigan vaqt. Reconciler har 30
# soniyada qaytadi, shuning uchun ulgurmagan amal yo'qolmaydi — keyingi
# tsiklda davom etadi. Byudjetsiz eski o'rnatishdagi minglab ortiqcha
# yo'lni tozalash tsiklni soatlab band qilardi.
SYNC_BUDGET_S = float(os.environ.get("MEDIAMTX_SYNC_BUDGET", "10"))

# Oxirgi tomoshabin ketgandan keyin manba shuncha ushlab turiladi.
# MediaMTX davomiyliklarni normallashtirib saqlaydi ("120s" -> "2m0s") —
# taqqoslash aynan mos kelishi uchun uning o'z shaklida yoziladi, aks
# holda har sinxronlashda keraksiz PATCH ketardi.
SOURCE_CLOSE_AFTER = os.environ.get("MEDIAMTX_CLOSE_AFTER", "2m0s")

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

WARM_TTL = 600.0       # soniya — sub yo'l: oxirgi so'rovdan keyin shuncha
# Asosiy oqim ham ko'rilayotganda issiq bo'lishi SHART. sourceOnDemand
# rejimida MediaMTX manbani ochib-yopib turadi (tomoshabin yo'q deb
# hisoblab, closeAfter bo'yicha) va tomosha aynan shunda uziladi.
# O'lchov bilan tasdiqlangan: qurilmadan to'g'ridan tortish 92/90 soniya
# toza o'tdi, always_on bilan MediaMTX orqali ham 90 soniya toza, lekin
# sourceOnDemand bilan muzlab qolardi.
#
# Muddat sub'nikidan qisqa: asosiy oqim ~1-4 Mbit/s va tomosha
# tugagandan keyin uzoq ushlab turishning ma'nosi yo'q. Tomosha davom
# etar ekan muddat har sinxronlash tsiklida yangilanadi.
WARM_MAIN_TTL = 180.0
WARM_LIMIT = 256       # bir vaqtda issiq yo'llar chegarasi

_warm: dict[str, float] = {}          # sub-slug -> muddati (monotonic)
_warm_lock = threading.Lock()


def mark_warm(slug: str, ttl: float = WARM_TTL) -> bool:
    """Yo'lni issiq qiladi (muddatni yangilaydi). Chegara to'lsa False.

    Issiq yo'lda `sourceOnDemand` o'chadi, ya'ni MediaMTX manbani
    ochib-yopmaydi — tomosha uzilmaydi.
    """
    now = time.monotonic()
    with _warm_lock:
        for key in [k for k, t in _warm.items() if t <= now]:
            _warm.pop(key, None)
        if slug not in _warm and len(_warm) >= WARM_LIMIT:
            return False
        _warm[slug] = max(_warm.get(slug, 0.0), now + ttl)
        return True


def is_warm(slug: str) -> bool:
    with _warm_lock:
        return _warm.get(slug, 0.0) > time.monotonic()


def warm_count() -> int:
    now = time.monotonic()
    with _warm_lock:
        return sum(1 for t in _warm.values() if t > now)


# ---------- ishlatilayotgan yo'llar (managed set) ----------
#
# Yo'llar talab bo'yicha yaratiladi (`ensure_path`), shuning uchun
# reconciler ularni "ortiqcha" deb o'chirib yubormasligi kerak. Har
# yaratilgan yo'l shu ro'yxatga muddat bilan yoziladi va `desired_paths`
# uni doimiy ro'yxatga qo'shadi. Muddati o'tgach yo'l o'z-o'zidan
# tozalanadi — kimdir yana ko'rsa qayta yaratiladi (9 ms).
#
# Muddat sourceOnDemandCloseAfter (1 daqiqa) dan ancha uzun: odam
# kamerani yopib qayta ochsa yo'l joyida turadi, ya'ni B2 dagi "issiq
# tutish" foydasi tarmoqni yemasdan olinadi.

MANAGED_TTL = 1800.0      # soniya — oxirgi ochilishdan keyin shuncha turadi
MANAGED_LIMIT = 2048      # bir vaqtda MediaMTX'da turadigan yo'l chegarasi

_managed: dict[str, float] = {}
_managed_lock = threading.Lock()

# Yo'l -> oxirgi bytesSent. Ikki tsikl orasidagi o'sish "kimdir ko'ryapti"
# degani. HLS tomoshabinini boshqa yo'l bilan aniqlab bo'lmaydi.
_sent: dict[str, int] = {}


def note_managed(slug: str) -> None:
    """Yo'l ishlatildi — reconciler uni o'chirmasin."""
    now = time.monotonic()
    with _managed_lock:
        if len(_managed) >= MANAGED_LIMIT and slug not in _managed:
            for key in [k for k, t in _managed.items() if t <= now]:
                _managed.pop(key, None)
        _managed[slug] = now + MANAGED_TTL


def is_managed(slug: str) -> bool:
    with _managed_lock:
        return _managed.get(slug, 0.0) > time.monotonic()


def managed_count() -> int:
    now = time.monotonic()
    with _managed_lock:
        return sum(1 for t in _managed.values() if t > now)


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
        # DIQQAT: bu qiymat FAQAT always_on ga bog'liq bo'lishi shart.
        #
        # Ilgari bu yerda `is_warm(...)` ham bor edi va yo'l issiqligi
        # so'nganda konfiguratsiya o'zgarardi. MediaMTX esa yo'l
        # konfiguratsiyasi o'zgarganda MANBANI QAYTA OCHADI — tomoshabin
        # uchun bu videoning uzilishi. Ya'ni issiqlikni shu yerda
        # ishlatish tomosha o'rtasida uzilishni KELTIRIB CHIQARARDI.
        #
        # Endi issiqlik boshqa vazifani bajaradi: yo'l MediaMTX
        # ro'yxatida QOLSIN (desired_paths ga qarang). Konfiguratsiyaning
        # o'zi esa kamera yaratilganidan keyin o'zgarmaydi.
        "sourceOnDemand": not cam.get("always_on"),
    }
    if conf["sourceOnDemand"]:
        # 20 soniya juda uzun edi: o'lik kamera shuncha vaqt "ulanmoqda…"
        # bo'lib turadi va foydalanuvchi uchun bu ham qotish. Lekin juda
        # qisqartirib ham bo'lmaydi — o'lchovda uzoq tarmoqdagi kamera
        # (A1, RTT ~50 ms) 10,5 soniyada ochilgan, 8 s uni butunlay
        # yo'qotardi. 12 s — o'shanaqa kamera sig'adi, o'liklari esa
        # ikki barobar tez rad javobini beradi.
        conf["sourceOnDemandStartTimeout"] = "12s"
        # MediaMTX davomiyliklarni normallashtirib saqlaydi ("60s" -> "1m0s").
        # Taqqoslash (ensure_path/push_to_api) aynan mos kelishi uchun
        # qiymatlar uning o'z shaklida yoziladi — aks holda har safar
        # keraksiz PATCH ketadi.
        # Oxirgi tomoshabindan keyin manba shuncha ushlab turiladi.
        # Bu — issiqlikning konfiguratsiyani o'zgartirmaydigan varianti:
        # kamera yopilib qayta ochilsa manba hali ulangan bo'ladi, ya'ni
        # ochilish bir zumda. Uzunroq qilish arzon emas (kamera ulangan
        # turadi), lekin 1 daqiqa qisqa edi.
        conf["sourceOnDemandCloseAfter"] = SOURCE_CLOSE_AFTER
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
    # Brauzer WebRTC uchun serverning yetib boradigan manzilini bilishi
    # kerak: uzoq tugunda bu uning o'z public_host'i, markaziy tugunda —
    # WEBRTC_HOSTS (yoki MEDIA_HOST).
    extra_hosts = ([node["public_host"]] if node.get("public_host")
                   else list(WEBRTC_HOSTS))
    config = {
        "logLevel": "info",
        # Sekin tomoshabin butun oqimni buzmasin (yuqoridagi izoh).
        "writeQueueSize": WRITE_QUEUE_SIZE,
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
        "webrtcLocalUDPAddress": f":{WEBRTC_UDP_PORT}" if WEBRTC_UDP_PORT else "",
        # ICE TCP zaxirasi — UDP yopiq tarmoqdagi tomoshabin HLS'ga
        # tushmasin (yuqoridagi izoh).
        "webrtcLocalTCPAddress": f":{WEBRTC_TCP_PORT}" if WEBRTC_TCP_PORT else "",
        "webrtcAdditionalHosts": extra_hosts,
        # Nginx (127.0.0.1) orqali kelgan so'rovlarda haqiqiy tomoshabin
        # IP'si X-Forwarded-For sarlavhasidan olinadi — auth va HLS
        # sessiyalari to'g'ri IP bilan ishlaydi.
        "webrtcTrustedProxies": ["127.0.0.1"],

        # HLS — WebRTC ishlamagan brauzerlar uchun zaxira.
        "hls": True,
        "hlsAddress": f":{hls_port}",
        # Oddiy fMP4 rejimi (lowLatency EMAS): LL-HLS'ning 200 ms'lik
        # qismlari oldindagi proxy'lar buferida qotib, qora ekran berardi.
        # Oddiy HLS 1 s'lik butun segmentlar bilan ishlaydi — har qanday
        # proxy orqali o'tadi; kechikish ~3-5 s, zaxira yo'l uchun maqbul.
        "hlsVariant": "fmp4",
        # Doimiy remux qilinsa xom H.265 yo'llari ham bekorga HLS'ga o'giriladi;
        # asosiy yo'l WebRTC bo'lgani uchun bunga hojat yo'q.
        "hlsAlwaysRemux": False,
        # 7×1 s segment — tez boshlanish va mo''tadil bufer.
        "hlsSegmentCount": 7,
        "hlsSegmentDuration": "1s",
        "hlsAllowOrigins": ["*"],
        "hlsTrustedProxies": ["127.0.0.1"],

        "rtmp": False,
        "srt": False,
        # MoQ (MediaMTX 1.20+) o'chiq: u QUIC uchun auto.key/auto.crt
        # yozmoqchi bo'ladi, konteynerda esa ishchi papkaga yozish huquqi
        # yo'q — MediaMTX shu xato bilan butunlay yiqilardi. Bizga MoQ
        # kerak emas (WebRTC + HLS yetarli).
        "moq": False,

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
    # Yo'l endi "ishlatilayotgan" — reconciler navbatdagi tsiklda uni
    # ortiqcha deb o'chirmaydi (desired_paths izohiga qarang).
    note_managed(slug)
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
    """MediaMTX'da DOIMIY turishi kerak bo'lgan yo'llar.

    Bu yerda hamma kamera YO'Q — va bu ataylab. MediaMTX har bir
    `paths/add` so'roviga butun konfiguratsiyani qayta yuklaydi, ya'ni
    bitta yo'l qo'shish narxi mavjud yo'llar soniga chiziqli o'sadi
    (o'lchov: 0 yo'lda 9 ms, 2400 yo'lda 297 ms). 5000 kamera = 10000
    yo'l bo'lsa, to'liq ro'yxatni yuborish soatlab davom etadi va
    MediaMTX har qayta ishga tushganda hammasi boshidan boshlanadi.

    Shuning uchun doimiy ro'yxat qisqa tutiladi:

      * o'girish shabloni (bitta regex, kameralar soniga bog'liq emas);
      * "doim tayyor" (always_on) kameralar — ular baribir ulangan turadi;
      * hozir ishlatilayotgan yo'llar (`note_managed` — ensure_path yozadi)
        va issiq sub yo'llar.

    Qolgan kameralar talab bo'yicha, ko'rish so'ralgan payt `ensure_path`
    bilan yaratiladi (9 ms) va bo'shab qolgach `push_to_api` tomonidan
    olib tashlanadi. Bu README'dagi tamoyilning o'zi: resurs kameralar
    soniga emas, ayni damda ko'rilayotganlar soniga qarab sarflanadi.

    `with_transcode=False` — uzoq tugunlar uchun: o'girish yo'llari
    `stream_launcher.py` ni chaqiradi, u esa faqat backend turgan
    mashinada bor. Uzoq tugun kameralarini H.264 da tuting.
    """
    wanted = dict(camera_paths(cameras)) if with_transcode else {}
    for cam in cameras:
        if not (cam.get("enabled") and cam.get("ip")):
            continue
        always = bool(cam.get("always_on"))
        if always or is_warm(cam["slug"]) or is_managed(cam["slug"]):
            wanted[cam["slug"]] = source_path(cam)
        sub = sub_variant(cam)
        if sub and (always or is_warm(sub["slug"]) or is_managed(sub["slug"])):
            wanted[sub["slug"]] = source_path(sub)
        if with_transcode and cam.get("transcode") and always:
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

    # Ayni damda tomosha qilinayotgan yo'l o'chirilmasin. `_managed` odatda
    # buni qoplaydi, lekin backend qayta ishga tushsa u bo'sh bo'ladi —
    # o'shanda tirik oqim uzilib qolardi.
    #
    # Ikki xil "band" bor va ularni chalkashtirmaslik kerak:
    #
    #   busy    — yo'l tirik (ready yoki o'quvchisi bor). Buni O'CHIRMAYMIZ.
    #   serving — chiqish baytlari o'syapti, ya'ni AYNI DAMDA kimdir
    #             ko'ryapti. Buni qayta SOZLAMAYMIZ.
    #
    # Nega ikkita: HLS tomoshabini MediaMTX'ning `readers` ro'yxatida
    # KO'RINMAYDI (har segment alohida HTTP so'rov, doimiy ulanish emas) —
    # o'lchov: readers=0, bytesSent=1,2 MB. Issiq yo'l esa doim ready
    # bo'lgani uchun `ready` ham "ko'rilyapti" degani emas.
    busy: set[str] = set()
    serving: set[str] = set()
    active = list_active_paths(api_base) or {}
    for name, item in active.items():
        if item.get("ready") or item.get("readers"):
            busy.add(name)
        sent = int(item.get("bytesSent") or 0)
        prev = _sent.get(name)
        _sent[name] = sent
        if item.get("readers") or (prev is not None and sent > prev):
            serving.add(name)
            # Ko'rilayotgan sub yo'l issiq bo'lib turaversin: aks holda
            # 10 daqiqadan keyin issiqlik so'nadi, konfiguratsiya o'zgaradi
            # va MediaMTX manbani qayta ochadi — tomosha uziladi.
            mark_warm(name, WARM_TTL if name.endswith(SUB_SUFFIX)
                            else WARM_MAIN_TTL)
    for name in [n for n in _sent if n not in active]:
        _sent.pop(name, None)                 # yopilgan yo'l hisobi kerak emas

    # Kameraga TEGISHLI BO'LA OLADIGAN yo'llar. `wanted` dan farqi: bu
    # yerda vaqtinchalik holat (issiqlik, managed) hisobga olinmaydi —
    # faqat "bunday kamera bormi va yoqilganmi" degan savol.
    #
    # Ikkovini ajratish shart: `busy` himoyasi (ko'rilayotgan yo'lni
    # o'chirmaslik) o'chirilgan kameraga TEGMASLIGI kerak. Aks holda
    # kamerani o'chirib qo'ysangiz ham uning yo'li tortib turaveradi —
    # band bo'lgani uchun hech qachon o'chirilmaydi.
    valid: set[str] = set(camera_paths(cameras)) if with_transcode else set()
    for cam in cameras:
        if not (cam.get("enabled") and cam.get("ip")):
            continue
        valid.add(cam["slug"])
        valid.add(cam["slug"] + TRANSCODE_SUFFIX)
        sub_cam = sub_variant(cam)
        if sub_cam:
            valid.add(sub_cam["slug"])

    ops: list[tuple[str, str, dict | None]] = []
    for name, conf in wanted.items():
        current = existing.get(name)
        if current is None:
            ops.append(("POST", f"/v3/config/paths/add/{name}", conf))
        elif any(current.get(k) != v for k, v in conf.items()):
            # Ko'rilayotgan yo'l qayta sozlanmaydi. MediaMTX yo'l
            # konfiguratsiyasi o'zgarganda manbani QAYTA OCHADI —
            # tomoshabin uchun bu videoning uzilishi bo'lib ko'rinadi.
            # O'zgarish yo'qolmaydi: yo'l bo'shashi bilan keyingi tsiklda
            # qo'llanadi.
            if name in serving:
                continue
            ops.append(("PATCH", f"/v3/config/paths/patch/{name}", conf))
    for name in existing:
        if name in wanted:
            continue
        if name not in valid:
            # Kamera o'chirilgan yoki bazadan olib tashlangan — yo'l band
            # bo'lsa ham ketadi. Aks holda u abadiy tortib turadi.
            ops.append(("DELETE", f"/v3/config/paths/delete/{name}", None))
        elif name not in busy:
            # Kamera joyida, faqat vaqtinchalik holati tugagan. Kimdir
            # ko'rayotgan bo'lsa tegilmaydi.
            ops.append(("DELETE", f"/v3/config/paths/delete/{name}", None))

    # Qo'shish/yangilash avval, o'chirish keyin: byudjet tugasa ham
    # ko'rilayotgan kameralar ishlaydigan holatda qoladi.
    ops.sort(key=lambda op: op[0] == "DELETE")

    # Vaqt byudjeti. MediaMTX har amalga butun konfiguratsiyani qayta
    # yuklaydi, ya'ni amal narxi mavjud yo'llar soniga qarab o'sadi —
    # eski o'rnatishdan qolgan minglab yo'lni bir tsiklda tozalamoqchi
    # bo'lsak tsikl soatlab osilib qolardi. Ulgurmagani keyingi tsiklda
    # davom etadi (reconciler har 30 soniyada qaytadi).
    deadline = time.monotonic() + SYNC_BUDGET_S
    SKIPPED = "__skip__"

    def _run(op: tuple[str, str, dict | None]):
        method, path, payload = op
        if time.monotonic() > deadline:
            return method, SKIPPED
        try:
            _api(method, path, payload, api_base=api_base)
            return method, None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return method, f"{path.rsplit('/', 1)[-1]}: {exc}"

    added = updated = removed = pending = 0
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        for method, error in pool.map(_run, ops):
            if error is SKIPPED:
                pending += 1
            elif error is not None:
                if method != "DELETE":     # o'chirishdagi xato jiddiy emas
                    errors.append(error)
            elif method == "POST":
                added += 1
            elif method == "PATCH":
                updated += 1
            else:
                removed += 1

    tail = f", {pending} ta keyingi tsiklga qoldi" if pending else ""
    if errors:
        return {"ok": False, "added": added, "updated": updated,
                "removed": removed, "pending": pending,
                "message": "Ba'zi yo'llar yuborilmadi: " + "; ".join(errors[:2]) + tail}
    return {"ok": True, "added": added, "updated": updated, "removed": removed,
            "pending": pending,
            "message": f"MediaMTX yangilandi (+{added} / ~{updated} / -{removed}){tail}"}

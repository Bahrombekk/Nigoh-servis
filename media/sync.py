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
import ctypes
import json
import os
import shutil
import socket
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
from core.security import hls_cdn_secret

# Loyiha ildizi — bu fayl media/ ichida turadi. mediamtx.yml ma'lumotlar
# katalogida (standart — ildiz; konteynerda NIGOH_DATA volume).
BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = DATA_DIR / "mediamtx.yml"
API_BASE = os.environ.get("MEDIAMTX_API", "http://127.0.0.1:9997")
API_TIMEOUT = 4.0

# HLS uchun "CDN kaliti" — `core.security.hls_cdn_secret()` beradi va
# nginx'dagi `proxy_set_header Authorization "Bearer <kalit>"` bilan
# AYNAN bir xil bo'lishi shart. Kalit `secret.key` dan hosil qilinadi,
# ya'ni HAR DOIM mavjud — mexanizm hech qachon "o'chiq" qolmaydi.
#
# Ilgari u `.env` dagi ixtiyoriy sozlama edi va ikkita joyda (.env va
# nginx) qo'lda bir xil yozilishi kerak edi. Amalda bu bajarilmadi:
# `.env` da qiymat yo'q edi, konfiguratsiyaga `hlsCDNSecret: ''` tushardi
# va nginx Bearer yuborsa ham MediaMTX sessiyali rejimda qolaverardi.
#
# Yoqilganda MediaMTX `Authorization: Bearer <kalit>` bilan kelgan
# so'rovni SESSIYASIZ o'tkazadi va manzillarga na `session=`, na
# `token=` qo'shadi.
#
# Nima uchun kerak: sessiyali rejimda manba qisqa uzilsa MediaMTX HLS
# muxerini yo'q qiladi, muxer bilan sessiya ham o'ladi va mijoz DOIMIY
# 401 oladi — o'lchovda o'sha manzil 96 marta ketma-ket 401 berdi va
# o'z-o'zidan tiklanmadi (faqat master qayta olinganda tiklanadi, bu esa
# buferni buzadi va ekranda uzilish bo'ladi). Bearer rejimida bekor
# bo'ladigan holat yo'q, shuning uchun manba qaytishi bilan o'sha manzil
# ishlashda davom etadi.
#
# DIQQAT: Bearer bilan kelgan so'rov bizning auth ilgagimizni chetlab
# o'tadi. Sarlavhani nginx qo'yadi, ya'ni tomoshabin chiptasini ham
# nginx tekshirishi shart (`auth_request` -> /api/auth/hls). Ikkalasini
# ham `python scripts/nginx_conf.py` to'g'ri yozib beradi. Sarlavha
# umuman kelmasa MediaMTX eski sessiyali yo'lda ishlaydi (ya'ni yuqorida
# tasvirlangan 401 qaytadi) — shu holat /api/auth/hls da aniqlanib
# jurnalga ogohlantirish bo'lib tushadi.

# Kamerani MediaMTX o'zi tortsinmi yoki FFmpeg tortsinmi.
#
# Standart holat — MediaMTX (yengil, jarayonsiz). Lekin MediaMTX RTSP
# keepalive yubormaydi, shuning uchun sessiyani `timeout=60` bilan
# e'lon qiladigan kameralar har 60 soniyada ulanishni uzadi va
# tomoshabin 401 oladi. FFmpeg keepalive yuboradi.
#
# DIQQAT: relay TEKIN EMAS. Shu o'rnatmada o'lchandi — bitta Dahua
# kanali (10.30.11.65), bir xil mashina, bir xil MediaMTX:
#
#     MediaMTX o'zi tortadi : 180 s da 0 uzilish,  bo'sh sekundlar  5 %
#     FFmpeg relay          :                      bo'sh sekundlar 51 %
#
# Ya'ni relay oqimni bo'lak-bo'lak qiladi: ma'lumot 5-8 soniya kelib,
# 3-6 soniya jim turadi. Bu FFmpeg bayrog'i bilan tuzalmaydi — sinaldi:
# `-flush_packets 1` 63 %, `-max_interleave_delta 0` yomonlashtirdi,
# `-muxdelay 0` 55 %. Tomoshabin uchun bu qotib-qotib turgan video.
#
# Shuning uchun relay HAMMAGA emas, faqat KERAKLI kameralarga yoqilsin:
#
#   RTSP_VIA_FFMPEG=1        — hamma kamera relay orqali (keng bolg'a);
#   FFMPEG_EXCLUDE=a,b       — o'shanda istisnolar;
#   FFMPEG_ONLY=a,b          — teskarisi va afzali: relay FAQAT shu
#                              kameralarga, qolganini MediaMTX o'zi
#                              tortadi. RTSP_VIA_FFMPEG kerak emas.
RTSP_VIA_FFMPEG = os.environ.get("RTSP_VIA_FFMPEG", "0") == "1"
FFMPEG_EXCLUDE = {s.strip() for s in
                  os.environ.get("FFMPEG_EXCLUDE", "").split(",") if s.strip()}
FFMPEG_ONLY = {s.strip() for s in
               os.environ.get("FFMPEG_ONLY", "").split(",") if s.strip()}


def pull_via_ffmpeg(slug: str) -> bool:
    """Shu yo'l FFmpeg relay orqali tortiladimi.

    `FFMPEG_ONLY` ro'yxatga kirgan kamera har doim relay orqali ketadi —
    u aynan shu kamera uchun qo'yilgan qaror (masalan `timeout=60` e'lon
    qilib, keepalive kutadigan registrator: MediaMTX keepalive yubormaydi
    va 60 soniyada uziladi, FFmpeg esa yuboradi).
    """
    if slug in FFMPEG_ONLY:
        return True
    return RTSP_VIA_FFMPEG and slug not in FFMPEG_EXCLUDE

RTSP_PORT = int(os.environ.get("MEDIAMTX_RTSP_PORT", "8554"))
HLS_PORT = int(os.environ.get("HLS_PORT", "8888"))
WEBRTC_PORT = int(os.environ.get("WEBRTC_PORT", "8889"))


def api_port(api_base: str | None = None) -> int:
    """MediaMTX API porti — `mediamtx.yml` ga aynan shu yoziladi.

    Bitta mashinada ikki tugun turishi mumkin (masalan asosiy tizim va
    mikroservis). Port qotib qolsa ikkinchisi o'z MediaMTX'ini ko'tara
    olmaydi: port band bo'lgani uchun ishga tushmaydi, `api_available`
    esa birinchisining API'sini ko'rib "hammasi joyida" deydi. Natijada
    ikkinchi tugunning reconcileri begona MediaMTX'ni boshqara boshlaydi
    va har tickda birinchisining yo'llarini o'chirib tashlaydi — tomosha
    bir necha soniyada uziladi. Shuning uchun manba yagona: MEDIAMTX_API.
    """
    tail = (api_base or API_BASE).split("//")[-1]
    _, _, port = tail.partition(":")
    port = port.split("/")[0].strip()
    return int(port) if port.isdigit() else 9997


API_PORT = api_port()
# Metrikalar porti ham tugun bilan birga suriladi — aks holda ikkinchi
# MediaMTX 9998 ni band deb topib yiqiladi.
METRICS_PORT = int(os.environ.get("MEDIAMTX_METRICS_PORT", str(API_PORT + 1)))

# WebRTC media (ICE) portlari. UDP — asosiy (eng samarali). TCP esa
# MediaMTX'da standart holda O'CHIQ, natijada UDP yopiq tarmoqda
# (korporativ firewall, ba'zi mobil operatorlar) brauzer jimgina HLS'ga
# tushardi — ya'ni eng sekin yo'lga. ICE ustuvorligi baribir avval UDP'ni
# sinaydi, TCP faqat zaxira bo'lib qoladi. Firewall'da bu portni ikkala
# protokol uchun oching. WEBRTC_TCP_PORT=0 — butunlay o'chirish.
#
# Port WEBRTC_PORT bilan BIRGA suriladi (METRICS_PORT API_PORT bilan
# surilgani kabi). Nima uchun: bitta mashinada ikkinchi o'rnatma bo'lsa
# operator .env da API/RTSP/HLS/WEBRTC portlarini o'zgartiradi, ICE porti
# esa e'tibordan chetda qolardi — va MediaMTX aynan shu bitta to'qnashuv
# sababli UMUMAN ko'tarilmasdi ("listen udp :8189: bind: Only one usage
# of each socket address..."), reconciler esa uni tinmay qayta urintirib
# turardi. Standart WEBRTC_PORT'da qiymat o'zgarmaydi: 8889 -> 8189.
# Siljish standart qiymatdan hisoblanadi; WEBRTC_PORT juda kichik
# bo'lsa (masalan 80) natija yaroqsiz portga tushmasin.
WEBRTC_ICE_PORT = max(1024, min(65535, 8189 + (WEBRTC_PORT - 8889)))
WEBRTC_UDP_PORT = int(os.environ.get("WEBRTC_UDP_PORT", str(WEBRTC_ICE_PORT)))
WEBRTC_TCP_PORT = int(os.environ.get("WEBRTC_TCP_PORT", str(WEBRTC_ICE_PORT)))

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


def _marshrut_manbasi() -> str:
    """Serverning HAQIQIY tarmoq manzili — marshrut jadvali bo'yicha.

    Nima uchun kerak: MediaMTX standart holda mashinadagi HAMMA
    interfeysni ICE nomzodi qilib e'lon qiladi va brauzer ularning
    birini tanlaydi. Tanlov bizga bog'liq emas — amalda VPN yoki
    virtual adapter tanlanadi. Shu o'rnatmada o'lchandi: brauzer ham,
    server ham BITTA kompyuterda turgani holda video Radmin VPN
    adapteri (fdfd::1a0c:3007) orqali ketdi va 36 soniyada 11 marta
    qotdi — vaqtning 22 %. Yo'l haqiqiy tarmoq kartasiga
    (192.168.136.69) o'tkazilganda qotish 11,7 % ga tushdi, kadr
    tezligi 21,6 dan 25,4 ga ko'tarildi. Paket yo'qolmagan (0) —
    virtual adapter kadrni yo'qotmaydi, kechiktiradi; WebRTC uchun
    kechikkan kadr — qotish.

    Interfeys nomi bo'yicha ro'yxat (`webrtcIPsFromInterfacesList`) bu
    ishga yaramaydi: Windows'da `socket.if_nameindex()` haqiqiy nom
    o'rniga `ethernet_0` kabi soxta nom qaytaradi va MediaMTX hech
    qanday nomzod bermay qoladi (sinaldi — ICE "new" holatida qotdi).
    Marshrut esa ikkala tizimda ham bir xil ishlaydi: UDP soketni
    ulash paket HECH QAYERGA yuborilmasdan yadroga "shu manzilga
    qaysi karta orqali chiqasan" degan savolni beradi.

    Topilmasa bo'sh satr — chaqiruvchi eski xatti-harakatda qoladi.
    """
    for maqsad in ("8.8.8.8", "1.1.1.1"):
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect((maqsad, 9))
            ip = sock.getsockname()[0]
            if ip and not ip.startswith("127."):
                return ip
        except OSError:
            continue
        finally:
            sock.close()
    return ""


def webrtc_ice_hosts() -> list[str]:
    """Brauzerga e'lon qilinadigan ICE manzillari.

    Tartib: operator bergan qiymat (WEBRTC_HOSTS/MEDIA_HOST/MEDIA_BASE)
    ustun — tashqi tomoshabin faqat o'sha domenga yetadi. Bo'sh bo'lsa
    serverning marshrut bo'yicha aniqlangan o'z manzili ishlatiladi.

    Ro'yxat bo'sh qaytishi ham to'g'ri natija: bunda interfeyslardan
    yig'ish O'CHIRILMAYDI (`write_config` ga qarang), ya'ni eski
    xatti-harakat saqlanadi va WebRTC ishlamay qolmaydi.
    """
    if WEBRTC_HOSTS:
        return list(WEBRTC_HOSTS)
    ip = _marshrut_manbasi()
    return [ip] if ip else []

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

# Talab bo'yicha manba ochilishini shuncha kutamiz. Ikki xil qiymat bor,
# chunki ikki yo'lning narxi ham har xil:
#
#   SOURCE_START_TIMEOUT — MediaMTX kameraga O'ZI ulanadi. O'lchov: uzoq
#       tarmoqdagi kamera (RTT ~50 ms) 10,5 soniyada ochilgan, shuning
#       uchun 12 s. 8 s uni butunlay yo'qotardi.
#
#   RELAY_START_TIMEOUT — zanjir uzunroq: MediaMTX -> python launcher
#       (venv importi, bazadan kamera) -> FFmpeg -> kameraga RTSP ulanish
#       -> birinchi keyframe. Shu o'rnatmada o'lchandi (FFmpeg'ning
#       o'zigina, launcher hisobga olinmagan): 10.30.11.65 -> 6,6 s,
#       10.30.45.73 -> 6,7 s, 10.30.17.67 -> 13,4-15,2 s (uch o'lchov).
#       Ya'ni 12 s bilan oxirgi kamera HECH QACHON ochilmasdi: MediaMTX
#       `runOnDemand command stopped: timed out` deb buyruqni o'ldirar,
#       pleyer qayta urinardi va tomoshabin uzluksiz "qayta ulanmoqda"
#       ko'rardi. O'lik kamera bu qiymatni kutmaydi — launcher avval TCP
#       tekshiruv qiladi (media/launcher.py: camera_reachable, 3 s).
# Manbadan paket kelmasa MediaMTX ulanishni shuncha kutadi (uning
# standarti 10s — uzun GOP'li kamerada kam; build_config izohiga qarang).
READ_TIMEOUT = os.environ.get("MEDIAMTX_READ_TIMEOUT", "30s")

# UDP orqali tortiladigan kameralar uchun qabul buferi (baytda).
# `build_config` izohiga qarang — yadroning `net.core.rmem_max` chegarasi
# ham shuncha bo'lishi kerak.
UDP_READ_BUFFER = int(os.environ.get("MEDIAMTX_UDP_READ_BUFFER", "8388608"))

SOURCE_START_TIMEOUT = os.environ.get("MEDIAMTX_START_TIMEOUT", "12s")
RELAY_START_TIMEOUT = os.environ.get("MEDIAMTX_RELAY_START_TIMEOUT", "30s")
# O'girish (`_h264`) zanjiri yana bir pog'ona uzun: uning manbasi xom
# yo'l, ya'ni avval o'sha yo'l ko'tarilishi kerak (RELAY_START_TIMEOUT),
# ustiga dekod/kodlash ishga tushishi qo'shiladi.
TRANSCODE_START_TIMEOUT = os.environ.get("MEDIAMTX_TRANSCODE_START_TIMEOUT", "45s")

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
def cuda_usable() -> bool:
    """NVIDIA drayveri shu jarayondan haqiqatan ochiladimi.

    `cuInit` — CUDA'ning eng birinchi qadami; FFmpeg ham `-hwaccel cuda`
    da aynan shuni chaqiradi. Kutubxona topilmasa yoki qurilma
    berilmagan bo'lsa shu yerda bilinadi, FFmpeg ishga tushishini
    kutmasdan.
    """
    for name in ("libcuda.so.1", "nvcuda.dll"):
        try:
            driver = ctypes.CDLL(name)
        except OSError:
            continue
        try:
            return driver.cuInit(0) == 0
        except (AttributeError, OSError):
            return False
    return False


@lru_cache(maxsize=1)
def has_nvenc() -> bool:
    """NVIDIA GPU orqali H.264 kodlash mumkinmi.

    Ikki shart bor va ikkalasi ham tekshiriladi: FFmpeg nvenc bilan
    yig'ilgan bo'lsin VA NVIDIA drayveri ochilsin.

    Ikkinchisi nega muhim: konteynerga GPU berilmagan bo'lsa
    `ffmpeg -encoders` ro'yxatida `h264_nvenc` baribir ko'rinadi (u
    build vaqtida qo'shilgan), lekin o'girish boshlanishi bilan
    "Cannot load libcuda.so.1" deb yiqiladi. Faqat ro'yxatga qaralsa
    H.265 kamera hech qachon ochilmaydi — har urinishda GPU yo'li
    tanlanib, har safar o'sha xato qaytadi. Drayver ochilmasa CPU
    (`libx264`) yo'liga tushamiz: sekinroq, lekin oqim ishlaydi.

    Yon foyda: GPU yo'q mashinada endi FFmpeg umuman chaqirilmaydi,
    ya'ni har bir o'girish jarayoni shuncha tez boshlanadi.
    """
    if not cuda_usable():
        return False
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
          "-analyzeduration", "1000000", "-probesize", "1000000"]

# UDP qabul buferi: kamera keyframe'ni portlash bilan yuboradi va standart
# soket buferi (net.core.rmem_default, odatda 208 KB) to'lib, paket tushib
# qoladi — tasvir "sinadi". O'lchov: shu buferisiz 2560x1440 H.265 oqimda
# sekundiga 75-212 RTP paket yo'qolardi.
# Tartibni tiklash navbati. `source_path` izohida UDP'ning asosiy
# muammosi TARTIB BUZILISHI deyilgan va uni FFmpeg `reorder_queue_size`
# bilan tiklashi aytilgan — lekin bayroq AMALDA berilmagan edi, ya'ni
# FFmpeg avtomatik (-1) qiymatda qolardi. Uzoq, yo'qotishli yo'lda bu
# kam: paket kechikib kelsa navbat allaqachon bo'shatilgan bo'ladi va
# kadr sinadi (ekranda yashil bloklar, yirtilgan tasvir).
#
# 2000 paket ~ 2,5 MB: 8 MB soket buferi bilan mos, kechikishga esa
# sezilarli hissa qo'shmaydi (navbat faqat TARTIBSIZ paket kelganda
# to'ladi, normal oqimda bo'sh turadi).
UDP_REORDER = int(os.environ.get("UDP_REORDER_QUEUE", "2000"))
_UDP_INPUT = ["-buffer_size", str(UDP_READ_BUFFER),
              "-reorder_queue_size", str(UDP_REORDER)]


def input_args(udp: bool = False) -> list[str]:
    """FFmpeg kirish argumentlari — kerakli RTSP transporti bilan.

    TCP standart (UDP'da paket yo'qoladi), lekin TCP'ni ko'tarmaydigan
    kamera uchun UDP yagona yo'l — core/db.py dagi `rtsp_udp` izohiga
    qarang.
    """
    if udp:
        return _INPUT + _UDP_INPUT + ["-rtsp_transport", "udp"]
    return _INPUT + ["-rtsp_transport", "tcp"]

# WebRTC uchun paketlar kichik bo'lsin — MediaMTX ularni qayta bo'lmasin.
_OUTPUT = ["-an", "-pkt_size", "1200", "-f", "rtsp", "-rtsp_transport", "tcp"]


# Relay chiqishi: `-an` YO'Q — kameraning barcha treklari (video + audio)
# borligicha o'tadi, ya'ni xom yo'lning mazmuni o'zgarmaydi.
_RELAY_OUTPUT = ["-c", "copy", "-pkt_size", "1200",
                 "-f", "rtsp", "-rtsp_transport", "tcp"]


def relay_args(src_url: str, dst_url: str, udp: bool = False) -> list[str]:
    """Oqimni qayta kodlashsiz uzatish (paketlar borligicha).

    Ishlatiladi: kamerani MediaMTX o'zi tortishi o'rniga FFmpeg tortadi.
    Nima uchun kerak bo'ldi — RTSP KEEPALIVE. Kamera sessiyani
    `Session: ...;timeout=60` bilan e'lon qiladi va shu muddat ichida
    keepalive kutadi; MediaMTX esa TCP orqali ma'lumot kelayotganini
    yetarli deb hisoblaydi va keepalive yubormaydi. Natijada kamera har
    60 soniyada ulanishni RST bilan uzadi (serverda tcpdump bilan
    o'lchandi: SETUP -> 59 soniyadan keyin RST, orada bitta ham
    OPTIONS/GET_PARAMETER yo'q). Har uzilishda HLS muxeri yo'q qilinadi,
    sessiya o'ladi va tomoshabin 401 oladi. FFmpeg esa keepalive
    yuboradi — o'lchov: shu kamerani ffmpeg 302 soniya uzilmasdan
    o'qidi, MediaMTX esa 54 soniyada uzildi.
    """
    return input_args(udp) + ["-i", src_url] + _RELAY_OUTPUT + [dst_url]


# O'girishda chiqish tezligi. ILGARI qat'iy `-rc cbr -b:v 3M` edi —
# manba nima bo'lishidan qat'i nazar. O'lchov bu qiymat IKKALA yo'nalishda
# ham noto'g'ri ekanini ko'rsatdi (SSIM 1,0 = manbaning aynan o'zi):
#
#   sub oqim, 720x576, kameradan 0,73 Mbit/s keladi:
#     cbr 3M          -> 3,01 Mbit/s   SSIM 0,991
#     vbr cq28 (yangi)-> 1,21 Mbit/s   SSIM 0,976     -60 % trafik
#
#   asosiy oqim, 2560x1440, kameradan 3,89 Mbit/s keladi:
#     cbr 3M          -> 3,05 Mbit/s   SSIM 0,962   <- sifat SHU YERDA yo'qoladi
#     vbr cq28 (yangi)-> 4,03 Mbit/s   SSIM 0,973
#
# Ya'ni 720x576 ga 3 Mbit/s isrof, 2560x1440 ga esa kam. Sabab oddiy:
# bitta raqam ikkala o'lchamga to'g'ri kelmaydi.
#
# Yechim — tezlikni emas, SIFATNI belgilash (`-cq`). Enkoder qancha kerak
# bo'lsa shuncha sarflaydi: sokin sub oqim arzon tushadi, harakatli
# asosiy oqim kerakligini oladi. `-maxrate` faqat yuqori chegara —
# portlashda kanalni bosib qo'ymasin (odatda tegilmaydi).
#
# cq 28 tanlandi: sub'da SSIM 0,976 qoladi (kuzatuv uchun ko'zga
# ko'rinmaydigan farq), trafik esa uch baravar kamayadi.
TRANSCODE_CQ = os.environ.get("TRANSCODE_CQ", "28")
# Chegaralar o'lchamga qarab: sub odatda <= D1, asosiy 2-8 MP.
TRANSCODE_MAX_SUB = os.environ.get("TRANSCODE_MAX_SUB", "2M")
TRANSCODE_MAX_MAIN = os.environ.get("TRANSCODE_MAX_MAIN", "8M")


def _ikki_barobar(tezlik: str) -> str:
    """"2M" -> "4M". Tanib bo'lmasa qiymatning o'zi qaytadi."""
    son = tezlik.rstrip("MKmk")
    birlik = tezlik[len(son):] or "M"
    try:
        return f"{int(float(son) * 2)}{birlik}"
    except ValueError:
        return tezlik


def transcode_args(src_url: str, dst_url: str, gpu: bool = True,
                   maxrate: str = TRANSCODE_MAX_MAIN,
                   udp: bool = False) -> list[str]:
    """Kamera H.265 bergan holat: dekodlash va qayta kodlash.

    H.265 va H.264 — bir-biriga o'xshamaydigan siqish usullari, shuning
    uchun oraliq qadamsiz o'girib bo'lmaydi: tasvirni ochib, qaytadan
    siqish shart. Buni yo'qotishning yagona yo'li — kamerani H.264 ga
    o'tkazish, shunda yuqoridagi `relay_args` ishlaydi.

    `maxrate` — yuqori chegara, nishon emas. Sifatni `TRANSCODE_CQ`
    belgilaydi (yuqoridagi o'lchovga qarang).
    """
    # Kirish vaqt belgisi kameraga ISHONMAYDI — paket kelgan paytdan
    # olinadi. Nima uchun (ishlab chiqarishda o'lchandi):
    #
    # Kamera kadr tezligini e'lon qilmasa (bazada `fps=0.0`), FFmpeg
    # vaqt bazasini xato oladi. Prezentatsiya vaqti real vaqtdan o'n
    # barobar tez yuradi va `-maxrate` "sekundiga" ma'nosini yo'qotadi:
    #
    #     kameradan kelgan sub oqim     0,63 Mbit/s
    #     o'girilgandan keyin chiqish  99,06 Mbit/s   (~150 barobar)
    #     kodlovchi CPU                35 %  (sog'lomlari 1 %)
    #
    # Natijasi tomoshabin uchun: MediaMTX HLS muxerini har 3 soniyada
    # o'ldiradi ("reached maximum segment size"), pleyer qayta ulanadi
    # va katak sinib turadi. Bitta shunday katak butun chiqishning 80 %
    # ini yeb, DEVORDAGI QOLGAN HAMMA katakni ham sindiradi.
    wallclock = ["-use_wallclock_as_timestamps", "1"]
    # Bufer nishondan ikki barobar: keyframe portlashi sig'sin, lekin
    # yo'l hech qachon o'nlab Mbit/s ga chiqmasin. Qattiq shift —
    # kodlovchi qanday adashsa ham kanalni bosa olmaydi.
    bufsize = _ikki_barobar(maxrate)
    if gpu:
        # Dekodlash ham, kodlash ham GPU'da — nusxalashsiz.
        video = wallclock + [
            "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
            "-i", src_url,
            "-c:v", "h264_nvenc", "-preset", "p1", "-tune", "ull",
            "-rc", "vbr", "-cq", TRANSCODE_CQ, "-b:v", "0",
            "-maxrate", maxrate, "-bufsize", bufsize,
        ]
    else:
        video = wallclock + [
            "-i", src_url,
            "-c:v", "libx264", "-preset", "ultrafast", "-tune", "zerolatency",
            "-crf", TRANSCODE_CQ, "-maxrate", maxrate, "-bufsize", bufsize,
        ]
    # Qisqa GOP — segment tezroq tayyor bo'ladi.
    return input_args(udp) + video + ["-g", "30", "-bf", "0"] + _OUTPUT + [dst_url]


def kadr_keladimi(url: str, sekund: float = 12.0,
                  udp: bool = False) -> bool:
    """Oqim HAQIQATAN kadr beradimi — DESCRIBE emas, o'qib ko'rish.

    Nima uchun kerak: RTSP DESCRIBE yolg'on gapiradi. O'lchangan holat —
    registratorning 4 va 8-kanali DESCRIBE'ga javob berib, SDP'da sub
    oqimni (hevc 704x576) e'lon qilardi, lekin SETUP/PLAY dan keyin
    bitta ham paket kelmasdi. `core.rtsp_probe.probe` shu sababli
    ikkalasini ham "sog'lom" deb ko'rsatgan edi, bazadagi `sub_codec`
    ham shundan H265 bo'lib qolgan.

    Shuning uchun tekshiruv paket darajasida: birinchi video paket
    kelsa — oqim bor. Kelmasa (yoki muddat tugasa) — yo'q.
    """
    exe = ffmpeg_path()
    if not exe:
        return False
    probe = exe.replace("ffmpeg", "ffprobe")
    # Transport kameraga qarab: bir qism kamera RTSP'ni TCP'da umuman
    # bermaydi va UDP'ga o'tkazilgan (`cameras.rtsp_udp`,
    # media/transport.py). Bu yerda qat'iy TCP yozilsa, o'sha kamera
    # "kadr bermayapti" bo'lib chiqardi — aslida transport noto'g'ri.
    cmd = [probe, "-hide_banner", "-loglevel", "error",
           "-rtsp_transport", "udp" if udp else "tcp",
           "-select_streams", "v:0", "-show_entries", "packet=pts_time",
           "-of", "csv=p=0", "-read_intervals", "%+2", "-i", url]
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           timeout=sekund)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return any(satr.strip() for satr in r.stdout.splitlines())


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


def relay_path(cam: dict) -> dict:
    """Kamerani FFmpeg tortadigan yo'l (qayta kodlashsiz, `-c copy`).

    MediaMTX manbani o'zi ochmaydi — talab bo'yicha launcher chaqiriladi,
    u kameraga ulanadi va oqimni shu yo'lga publish qiladi. Sababi
    `relay_args()` izohida: MediaMTX RTSP keepalive yubormaydi va
    `timeout=60` e'lon qilgan kamera har 60 soniyada ulanishni uzadi.

    Konfiguratsiya kamera ma'lumotlariga BOG'LIQ EMAS (parol ham yo'q) —
    launcher hammasini bazadan o'zi oladi. Shu sababli kamera tahrirlansa
    ham yo'l konfiguratsiyasi o'zgarmaydi, ya'ni tomosha o'rtasida manba
    qayta ochilmaydi.
    """
    conf = {
        "runOnDemand": _launcher(cam["slug"]),
        "runOnDemandRestart": True,
        "runOnDemandStartTimeout": RELAY_START_TIMEOUT,
        "runOnDemandCloseAfter": SOURCE_CLOSE_AFTER,
    }
    return conf


def source_path(cam: dict) -> dict:
    """Kamerani MediaMTX o'zi tortadigan yo'l.

    FFmpeg ishlatilmaydi: MediaMTX RTSP'ni to'g'ridan-to'g'ri oladi. Bu ham
    tezroq (jarayon ishga tushirilmaydi), ham yengilroq (~220 MB o'rniga
    bir necha MB), ham ishonchliroq — FFmpeg nusxalashda B-kadrli H.264
    oqimni buzib yuborardi.
    """
    # UDP'ga o'tkazilgan kamera FFmpeg orqali tortiladi — MediaMTX'ning
    # o'zi UDP'ni yomon o'qiydi. O'lchov (bitta 2560x1440 H.265 kamera,
    # boshqa iste'molchi yo'q):
    #
    #   MediaMTX to'g'ridan UDP — sekundiga 80-220 "RTP packets lost" va
    #       uzluksiz "invalid fragmentation unit" (kadrlar sinadi);
    #   FFmpeg UDP -> lokal RTSP — 20 soniyada 470 kadr, DEKOD XATOSI 0.
    #
    # Sababi yo'qotish emas, TARTIB BUZILISHI: uzoq tarmoqda UDP paketlar
    # aralashib keladi, FFmpeg ularni navbat bilan tiklaydi
    # (`reorder_queue_size`), MediaMTX esa tiklamaydi. Relay chiqishi
    # lokal TCP, ya'ni MediaMTX'ga allaqachon tartiblangan oqim tushadi.
    if pull_via_ffmpeg(cam["slug"]) or cam.get("rtsp_udp"):
        return relay_path(cam)

    conf = {
        "source": build_rtsp_url(
            cam["ip"], cam["port"], cam.get("rtsp_path") or "/",
            cam.get("username") or "", cam.get("password") or "",
        ),
        # UDP'da paketlar yo'qoladi va tasvir buziladi — shuning uchun
        # standart TCP. ISTISNO: TCP'ni umuman ko'tarmaydigan kamera
        # (core/db.py `rtsp_udp` izohi) — unda tanlov "buzuq tasvir"
        # bilan "tasvir yo'q" o'rtasida, ya'ni UDP yagona yo'l. Yo'qotishni
        # kamaytirish uchun MediaMTX'ning UDP qabul buferi kattalashtirilgan
        # (`udpReadBufferSize`, build_config).
        "rtspTransport": "udp" if cam.get("rtsp_udp") else "tcp",
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
        conf["sourceOnDemandStartTimeout"] = SOURCE_START_TIMEOUT
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
            "runOnDemandStartTimeout": TRANSCODE_START_TIMEOUT,
            "runOnDemandCloseAfter": "1m0s",   # MediaMTX normallashtirgan shakl
        },
        # Devor (mozaika) — `wall_<kalit>` so'ralganda mosaic launcher
        # ishga tushadi va tanlangan kameralarni bitta oqimga birlashtiradi.
        # Bitta shablon barcha devorlarga yetadi (kameralar soniga bog'liq
        # emas); talab bo'yicha ochiladi, bo'shab qolsa yopiladi.
        "~^wall_[a-f0-9]+$": {
            "runOnDemand": _launcher("$MTX_PATH"),
            "runOnDemandRestart": True,
            "runOnDemandStartTimeout": "45s",
            "runOnDemandCloseAfter": "30s",
        },
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
    # Uzoq tugunda API porti tugunning o'z manzilidan olinadi.
    api_prt = api_port(node.get("api_base")) if remote else API_PORT
    metrics_prt = api_prt + 1 if remote else METRICS_PORT
    # Brauzer WebRTC uchun serverning yetib boradigan manzilini bilishi
    # kerak: uzoq tugunda bu uning o'z public_host'i, markaziy tugunda —
    # WEBRTC_HOSTS (yoki MEDIA_HOST).
    extra_hosts = ([node["public_host"]] if node.get("public_host")
                   else webrtc_ice_hosts())
    config = {
        "logLevel": "info",
        # Sekin tomoshabin butun oqimni buzmasin (yuqoridagi izoh).
        "writeQueueSize": WRITE_QUEUE_SIZE,
        "api": True,
        "apiAddress": f":{api_prt}" if remote else f"127.0.0.1:{api_prt}",

        # Prometheus metrikalari (oqimlar, tomoshabinlar, baytlar) —
        # keyinchalik Grafana ulash uchun tayyor turadi.
        "metrics": True,
        "metricsAddress": (f":{metrics_prt}" if remote
                           else f"127.0.0.1:{metrics_prt}"),

        # Kirish nazorati: har bir o'qish so'rovini backend tekshiradi —
        # saytdan berilgan chiptasiz oqim ochilmaydi. Backend ishlamayotgan
        # bo'lsa MediaMTX hamma so'rovni rad etadi (yopiq holatda xavfsiz).
        # API lokal portda va shusiz ham faqat 127.0.0.1 dan ochiq.
        "authMethod": "http",
        "authHTTPAddress": auth_url or STREAM_AUTH_URL,
        "authHTTPExclude": [
            {"action": "api"}, {"action": "metrics"}, {"action": "pprof"},
        ],

        # Manbadan shuncha vaqt bitta paket kelmasa MediaMTX ulanishni
        # uzadi. Standart 10 soniya — uzun GOP'li kamera uchun bu KAM:
        # shu o'rnatmada o'lchandi, bitta Dahua kanali ma'lumotni har 6-8
        # soniyada portlatib beradi va bitta tebranish yetarli edi —
        # `closed: read tcp ...: i/o timeout`, publisher o'ladi, tomosha
        # uziladi. O'lik manbani bu qiymat yashirmaydi: reconciler uni
        # STALL_AFTER (20 s) da o'zi aniqlaydi va hodisa yozadi.
        "readTimeout": READ_TIMEOUT,

        "rtsp": True,
        "rtspAddress": f":{rtsp_port}",
        # Bu — MediaMTX'ning RTSP SERVERI qabul qiladigan transportlar
        # (ichki publish va o'qish 127.0.0.1 orqali ketadi, u yerda TCP
        # eng to'g'ri tanlov). Kameradan TORTISH transporti alohida va
        # yo'l bo'yicha beriladi (`source_path`: rtspTransport).
        "rtspTransports": ["tcp"],
        # TCP'ni ko'tarmaydigan kameralar UDP'da tortiladi (core/db.py:
        # rtsp_udp). Standart soket buferi (208 KB) keyframe portlashiga
        # kichik — o'lchovda sekundiga 75-212 RTP paket yo'qolardi va
        # tasvir sinardi. 8 MB bufer buni yo'qotadi.
        #
        # DIQQAT: yadro buni `net.core.rmem_max` bilan cheklaydi. Server
        # standartda 208 KB beradi, ya'ni sozlama o'z-o'zicha yetarli
        # emas — deploy/README.md dagi sysctl ham qo'yilishi kerak.
        "udpReadBufferSize": UDP_READ_BUFFER,

        # WebRTC — asosiy yo'l, eng tez ochiladi.
        "webrtc": True,
        "webrtcAddress": f":{webrtc_port}",
        "webrtcAllowOrigins": ["*"],
        "webrtcLocalUDPAddress": f":{WEBRTC_UDP_PORT}" if WEBRTC_UDP_PORT else "",
        # ICE TCP zaxirasi — UDP yopiq tarmoqdagi tomoshabin HLS'ga
        # tushmasin (yuqoridagi izoh).
        "webrtcLocalTCPAddress": f":{WEBRTC_TCP_PORT}" if WEBRTC_TCP_PORT else "",
        "webrtcAdditionalHosts": extra_hosts,
        # Manzil aniq berilgan bo'lsa, INTERFEYSLARDAN nomzod yig'ish
        # o'chadi. Nima uchun: MediaMTX standart holda mashinadagi HAMMA
        # interfeysni ICE nomzodi qilib e'lon qiladi — Radmin VPN,
        # Tailscale, WSL vEthernet, Teredo ham. ICE ularning birini
        # tanlashi mumkin va tanlaydi ham.
        #
        # Shu o'rnatmada o'lchandi (10.30.11.71, brauzer ham server ham
        # BITTA kompyuterda):
        #
        #   Radmin VPN adapteri orqali : 21,6 kadr/s, 36 s da 11 qotish,
        #                                jami 7,9 s — vaqtning 22 %
        #   haqiqiy tarmoq kartasi     : 25,4 kadr/s, 47 s da 5,5 s
        #                                qotish — vaqtning 11,7 %
        #
        # Ikkalasida ham yo'qolgan paket 0 — virtual adapter paketni
        # yo'qotmaydi, kechiktiradi; WebRTC uchun bu qotish demak.
        #
        # DIQQAT: ro'yxat bo'sh bo'lsa TEGILMAYDI. O'chirib qo'yilsa
        # MediaMTX umuman nomzod bermaydi va WebRTC ulanmaydi — bu ham
        # shu yerda o'lchandi (ICE "new" holatida qotib qoldi).
        "webrtcIPsFromInterfaces": not extra_hosts,
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
        "hlsCDNSecret": hls_cdn_secret(),
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


# API javob berdi, lekin bu MediaMTX BIZNIKI emas.
#
# Nima uchun alohida holat kerak: reconciler "javob bermayapti" degan
# javobni ko'rsa o'zining MediaMTX'ini ko'taradi, "hammasi joyida"
# degan javobni ko'rsa yo'llarni kelishtiradi — begona instansiyada
# ikkalasi ham xato. Kelishtirish esa halokatli: `push_to_api` bizning
# kameralarimizda yo'q yo'llarni ORTIQCHA deb biladi va o'chiradi, ya'ni
# har 30 soniyada begona o'rnatmaning barcha yo'llarini nurga aylantiradi
# (o'lchov: shu mashinada ikkita loyiha bitta 9997-portni bo'lishgan va
# jurnal `+3 / ~1 / -43` ni tinmay takrorlagan — tomoshabin uchun bu
# har yarim daqiqada uziladigan, qotib qoladigan video edi).
FOREIGN = "begona"


def api_status(api_base: str | None = None) -> str:
    """`ok` | `yoq` (javob bermayapti) | `begona` (boshqa o'rnatmaniki).

    Egalik `authHTTPAddress` bo'yicha aniqlanadi: MediaMTX har ulanishda
    ruxsatni AYNAN SHU manzildan so'raydi, ya'ni u qaysi backend'ga
    bo'ysunishini ochiq aytib turadi. Bizniki bo'lsa — o'zimiz yozgan
    `STREAM_AUTH_URL`. Boshqa manzil — boshqa backend'ning MediaMTX'i,
    unga tegishga haqqimiz yo'q.
    """
    try:
        conf = _api("GET", "/v3/config/global/get", api_base=api_base)
    except (urllib.error.URLError, OSError, ValueError):
        return "yoq"
    theirs = (conf or {}).get("authHTTPAddress") or ""
    # Bo'sh qiymat — auth umuman sozlanmagan (eski yoki qo'lda yozilgan
    # konfiguratsiya). Bunda egalikni bilib bo'lmaydi; ilgarigidek
    # ishonamiz, aks holda ishlab turgan o'rnatmalar to'satdan to'xtardi.
    if theirs and theirs != STREAM_AUTH_URL:
        return FOREIGN
    return "ok"


def api_available(api_base: str | None = None) -> bool:
    """MediaMTX javob beryaptimi VA u bizniki mi."""
    return api_status(api_base) == "ok"


def _foreign_message(api_base: str | None = None) -> str:
    """Operatorga aniq ko'rsatma — taxmin qilishga o'rin qolmasin."""
    try:
        conf = _api("GET", "/v3/config/global/get", api_base=api_base) or {}
    except (urllib.error.URLError, OSError, ValueError):
        conf = {}
    return (f"{api_base or API_BASE} dagi MediaMTX boshqa o'rnatmaniki "
            f"(ruxsatni {conf.get('authHTTPAddress') or '?'} dan so'rayapti, "
            f"bizniki {STREAM_AUTH_URL}) — unga tegilmadi. Yo shu ikkinchi "
            f"xizmatni to'xtating, yo bu o'rnatmaga o'z MediaMTX'ini bering "
            f"(MEDIAMTX_API va MEDIAMTX_RTSP_PORT/HLS_PORT/WEBRTC_PORT "
            f"boshqa portlarga)")


def needs_replace(current: dict | None, wanted: dict) -> bool:
    """Yo'lning TURI o'zgardimi (MediaMTX o'zi tortadi <-> FFmpeg relay).

    PATCH faqat berilgan maydonlarni almashtiradi. Kamera UDP'ga
    o'tkazilganda yo'l `source` dan `runOnDemand` ga o'tadi va PATCH
    qilinsa eski `source` JOYIDA QOLADI: MediaMTX kamerani o'zi ham
    tortadi, launcher ham tortadi — kameraga ikkita ulanish, oqim esa
    ikkalasi orasida sakraydi. Bunday holatda butun konfiguratsiya
    almashtiriladi (`replace`).
    """
    return (bool((current or {}).get("runOnDemand"))
            != bool(wanted.get("runOnDemand")))


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
        elif needs_replace(current, wanted):
            _api("POST", f"/v3/config/paths/replace/{slug}", wanted,
                 api_base=api_base)
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
    # Begona MediaMTX'ga TEGMAYMIZ. Tekshiruv aynan shu yerda: quyida
    # "bizning kameralarda yo'q" degan yo'llar o'chiriladi, ya'ni boshqa
    # o'rnatmaning MediaMTX'ida bu funksiya butun konfiguratsiyani
    # supurib tashlaydi. Chaqiruvchi reconciler ham, admin paneli ham
    # bo'lishi mumkin — shuning uchun himoya chaqiruvchida emas, shu
    # yerda turadi.
    if api_status(api_base) == FOREIGN:
        return {"ok": False, "added": 0, "updated": 0, "removed": 0,
                "pending": 0, "message": _foreign_message(api_base)}

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
            if needs_replace(current, conf):
                ops.append(("POST", f"/v3/config/paths/replace/{name}", conf))
            else:
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

    def _turi(path: str) -> str:
        """Amal nima qildi — hisobot uchun. HTTP metodidan emas, MANZILDAN:
        yo'l turi o'zgarganda `replace` ham POST bilan ketadi, lekin u
        yangi yo'l qo'shish emas, mavjudini yangilash."""
        if "/add/" in path:
            return "added"
        if "/delete/" in path:
            return "removed"
        return "updated"

    def _run(op: tuple[str, str, dict | None]):
        method, path, payload = op
        kind = _turi(path)
        if time.monotonic() > deadline:
            return kind, SKIPPED
        try:
            _api(method, path, payload, api_base=api_base)
            return kind, None
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return kind, f"{path.rsplit('/', 1)[-1]}: {exc}"

    added = updated = removed = pending = 0
    errors: list[str] = []
    with ThreadPoolExecutor(max_workers=16) as pool:
        for kind, error in pool.map(_run, ops):
            if error is SKIPPED:
                pending += 1
            elif error is not None:
                if kind != "removed":      # o'chirishdagi xato jiddiy emas
                    errors.append(error)
            elif kind == "added":
                added += 1
            elif kind == "updated":
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

"""Nigoh — kamera qaysi RTSP transportida ishlashini O'ZI aniqlaydi.

Nima uchun kerak bo'ldi. Standart tanlov — TCP (interleaved): UDP'da
paket yo'qoladi va tasvir sinadi. Lekin kameralarning bir qismi TCP'ni
umuman ko'tarmaydi: DESCRIBE/SETUP/PLAY ga 200 OK beradi, keyin ulanishni
1-2 soniyada o'zi yopadi.

O'lchov (ishlab chiqarish o'rnatmasi, 155 ta yoqilgan kamera, har biriga
8 soniyalik FFmpeg tortish):

    TCP'da ishladi          113
    TCP yiqildi, UDP ishladi 27   (8 soniyada ~190 kadr)
    ikkalasi ham yiqildi     15   (kamera o'chiq / manzili eskirgan)

Ishlab chiqaruvchiga bog'liq emas (dahua ham, holowits ham bor) va
subnetga ham bog'liq emas — bir xil subnetda ishlaydigani ham,
yiqiladigani ham uchraydi. Ya'ni buni oldindan ro'yxat qilib yozib
bo'lmaydi: qaror har bir kamera uchun o'lchov bilan olinadi va bazada
(`cameras.rtsp_udp`) saqlanadi.

Tashqaridan bu nosozlik "kamera ochilmayapti" bo'lib ko'rinardi: MediaMTX
HLS muxerini yaratadi, manba darhol o'ladi, muxer birinchi segmentni
bermay yo'q qilinadi va brauzer `index.m3u8` ga BO'SH TANALI 500 oladi.
Jurnalda esa faqat `[RTSP source] unexpected EOF` qolardi.

Tekshiruv qachon ishga tushadi: reconciler yo'l uzoq vaqt "tayyor"
bo'lmayotganini ko'rganda (`media/reconciler.py`). Ya'ni normal ishlayotgan
kameralar hech qachon sinovdan o'tmaydi va kameraga ortiqcha ulanish
bo'lmaydi.
"""
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone

from core import events, security
from core.db import get_db
from core.log import log
from core.rtsp_probe import build_rtsp_url

from . import sync

# Sinov uzunligi. Qisqa bo'lsa ishlaydigan kamerani ham "yiqildi" deb
# belgilash xavfi bor (sekin ochiladigan kamera o'lchovda 10,5 soniyada
# ulangan), uzun bo'lsa bitta kameraning tashxisi yarim daqiqaga cho'ziladi.
PROBE_SECONDS = float(os.environ.get("TRANSPORT_PROBE_SECONDS", "8"))
# Shu kadrdan ko'p kelsa transport ishlayapti. 1-2 kadr yetarli emas:
# yiqiladigan ulanish ham uzilishdan oldin bitta kadr berib ulguradi.
MIN_FRAMES = 5
# Transport "ishlaydi" deyish uchun shuncha KETMA-KET urinish kadr
# berishi kerak.
#
# Nima uchun bitta urinish yetarli emas. Nosozlik ko'pincha turg'un
# emas: shu flot bir martalik o'lchov bilan ikki marta sinalganda
# birinchisida 42, ikkinchisida 67 ta kamera "TCP bermadi" chiqdi. Aniq
# misol — bitta kamera bir martalik sinovda TCP'ni "ishlaydi" deb
# ko'rsatdi, lekin tomoshabin darajasida 30 ta so'rovdan atigi 6 tasi
# ochildi. Ya'ni "goh ishlaydi" — bu tomoshabin uchun "ishlamaydi".
#
# Shuning uchun ikkala tomonga ham bir xil baland bo'sag'a: TCP'ni
# saqlab qolish uchun ham, UDP'ga o'tish uchun ham 2/2 kerak.
STABLE_TRIES = 2
# Ikkinchi transport hozirgisidan SHUNCHA baravar ko'p kadr bersa,
# hozirgisi "ishlayapti" bo'lsa ham o'tiladi.
#
# Nima uchun: nosozlik doim "umuman bermaydi" ko'rinishida emas. O'lchov —
# bitta kamera TCP'da 8 soniyada 28 kadr (~3,5 fps), UDP'da 188 kadr
# (~23 fps) bergan. TCP rasman "ishlayapti", lekin MediaMTX HLS
# segmentini yig'ib ulgurmasdan manba uzilgan va tomoshabin faqat 500
# ko'rgan. 3 baravar — shovqindan ancha baland bo'sag'a: sog'lom
# kamerada ikkala transport ham bir xil chiqadi (o'lchovda 150-240 kadr).
DEGRADED_RATIO = float(os.environ.get("TRANSPORT_DEGRADED_RATIO", "3"))
# Bitta kamerani qayta-qayta sinamaymiz — har sinov kameraga bir necha
# ulanish va yarim daqiqagacha vaqt demakdir.
RETRY_AFTER = float(os.environ.get("TRANSPORT_RETRY_AFTER", "3600"))
# Bir vaqtda nechta kamera sinaladi.
#
# Chegara SHART: nosozlik odatda yakka emas (o'lchovda o'nlab kamera
# birdan yiqilgan), va hammasi birdan sinovga tushsa o'nlab FFmpeg
# ko'tarilib, tarmoqni o'zimiz bosardik — o'sha bosim ostida sog'lom
# kamera ham "TCP bermadi" bo'lib chiqadi. Ya'ni chegarasiz sinov o'z
# natijasini o'zi buzadi. Navbatga tushmagani keyingi tsiklda sinaladi.
MAX_PARALLEL = int(os.environ.get("TRANSPORT_MAX_PARALLEL", "2"))

_lock = threading.Lock()
_last_try: dict[str, float] = {}      # slug -> oxirgi sinov (monotonic)
_busy: set[str] = set()               # ayni damda sinovdan o'tayotganlar

_FRAME_RE = re.compile(r"frame=\s*(\d+)")


def _frames(url: str, transport: str) -> int:
    """Shu transport bilan necha kadr keladi (FFmpeg bilan o'lchanadi).

    Nima uchun FFmpeg: `core/rtsp_probe.py` faqat RTSP muloqotini
    tekshiradi (DESCRIBE/SETUP), bu nosozlik esa aynan PLAY dan keyin
    boshlanadi — kadrlar oqmaguncha ko'rinmaydi.
    """
    exe = sync.ffmpeg_path()
    if not exe or not url:
        return -1
    args = [exe, "-hide_banner", "-loglevel", "error", "-stats",
            "-rtsp_transport", transport]
    if transport == "udp":
        args += ["-buffer_size", str(sync.UDP_READ_BUFFER)]
    args += ["-i", url, "-t", str(int(PROBE_SECONDS)), "-an",
             "-c", "copy", "-f", "null", "-"]
    try:
        out = subprocess.run(args, capture_output=True, text=True,
                             timeout=PROBE_SECONDS + 20)
    except (OSError, subprocess.SubprocessError):
        return -1
    found = _FRAME_RE.findall(out.stderr or "")
    return int(found[-1]) if found else 0


def _stable(url: str, transport: str) -> bool:
    """Shu transport ISHONCHLI ishlaydimi (ketma-ket urinishlarning barchasi).

    Birinchi muvaffaqiyatsizlikda to'xtaydi — sog'lom kamerada ham,
    o'lik kamerada ham ortiqcha ulanish bo'lmasin.
    """
    return _measure(url, transport) > 0


def _measure(url: str, transport: str) -> int:
    """Shu transport necha kadr beradi — urinishlarning ENG YOMONI.

    0 — ishonchsiz: urinishlarning birortasi kadr bermadi. Aynan eng
    yomon urinish olinadi, chunki tomoshabin uchun ham o'sha muhim:
    "goh ishlaydi" — bu ishlamaydi degani.
    """
    eng_yomon = 0
    for _ in range(STABLE_TRIES):
        kadr = _frames(url, transport)
        if kadr <= MIN_FRAMES:
            return 0
        eng_yomon = kadr if not eng_yomon else min(eng_yomon, kadr)
    return eng_yomon


def _camera(slug: str):
    with get_db() as db:
        return db.execute(
            "SELECT id, slug, external_id, ip, port, username, password_enc, "
            "rtsp_path, rtsp_udp FROM cameras WHERE slug = ?", (slug,)).fetchone()


def _remember(row, udp: bool) -> None:
    """Qarorni bazaga yozadi va hodisa qoldiradi.

    Yo'l konfiguratsiyasini bu yerda YANGILAMAYMIZ: `ensure_path`
    (ko'rish so'ralganda) va reconciler'ning `push_to_api` si farqni
    o'zi ko'radi va MediaMTX'ni kelishtiradi.
    """
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_db() as db:
        db.execute("UPDATE cameras SET rtsp_udp = ? WHERE id = ?",
                   (1 if udp else 0, row["id"]))
        events.add(db, "transport", slug=row["slug"],
                   detail=f"RTSP transporti {'UDP' if udp else 'TCP'} ga "
                          f"o'tkazildi (avtomatik o'lchov)")
    log("transport", "rtsp_transport_switched", slug=row["slug"],
        transport="udp" if udp else "tcp", at=at)


def check(slug: str) -> str | None:
    """Kamerani ikkala transportda sinab ko'radi va qarorni saqlaydi.

    Qaytaradi: `"udp"` / `"tcp"` — o'zgarish bo'lsa, `None` — o'zgarish
    yo'q yoki sinash mumkin bo'lmadi. Sekin ishlaydi (~16 soniya),
    shuning uchun faqat fon thread'idan chaqiriladi.
    """
    row = _camera(slug)
    if row is None or not row["ip"]:
        return None
    url = build_rtsp_url(row["ip"], row["port"] or 554,
                         row["rtsp_path"] or "/", row["username"] or "",
                         security.decrypt(row["password_enc"]))
    hozir_udp = bool(row["rtsp_udp"])
    # Avval HOZIRGI transport sinaladi: ishonchli ishlayotgan bo'lsa
    # muammo boshqa yoqda (kamera o'chiq, parol o'zgargan) va ikkinchi
    # transportni umuman bezovta qilmaymiz.
    hozirgi = "udp" if hozir_udp else "tcp"
    boshqa = "tcp" if hozir_udp else "udp"
    hozirgi_kadr = _measure(url, hozirgi)
    boshqa_kadr = _measure(url, boshqa)
    # O'lchov HAR DOIM jurnalga tushadi, qaror o'zgarmagan bo'lsa ham.
    # Aks holda "nega bu kamera ochilmayapti, tekshiruv nima dedi?"
    # degan savolga javob qolmaydi — tashxis jimgina yo'qoladi.
    log("transport", "probe", slug=slug,
        **{hozirgi: hozirgi_kadr, boshqa: boshqa_kadr},
        sekund=PROBE_SECONDS, hozirgi=hozirgi)
    if boshqa_kadr == 0:
        # Ikkinchisi ishonchsiz — o'tishning ma'nosi yo'q. Hozirgisi ham
        # bermayotgan bo'lsa, bu transport muammosi emas (kamera javob
        # bermayapti yoki butun tarmoq nosoz), va tirik transportni o'lik
        # kameraga qarab almashtirish keyin faqat chalkashtiradi.
        return None
    if hozirgi_kadr and boshqa_kadr < DEGRADED_RATIO * hozirgi_kadr:
        return None                    # hozirgisi yetarlicha yaxshi ishlayapti
    _remember(row, udp=(boshqa == "udp"))
    return boshqa


def _run(slug: str) -> None:
    try:
        check(slug)
    except Exception as exc:                      # fon thread'i o'lmasin
        log("transport", "probe_failed", level="warning", slug=slug,
            error=str(exc))
    finally:
        with _lock:
            _busy.discard(slug)


def busy(slug: str) -> bool:
    """Shu kamera ayni damda transport sinovida turibdimi.

    Boshqa avtomatik hukmlar sinov tugashini kutishi uchun kerak:
    sinov davomida kamera TCP'da kadr bermasligi NORMAL holat, u
    aynan shuni o'lchayapti.
    """
    with _lock:
        return slug in _busy


def request(slug: str) -> bool:
    """Fonda sinov buyuradi (chaqiruvchini kutdirmaydi).

    Bir xil kamera uchun RETRY_AFTER ichida bir marta — aks holda
    ochilmayotgan kamera har tsiklda ikkita FFmpeg ko'tarardi.
    """
    now = time.monotonic()
    with _lock:
        if slug in _busy or len(_busy) >= MAX_PARALLEL:
            # Navbat to'lgani "sinaldi" degani EMAS — vaqt belgilanmaydi,
            # kamera keyingi tsiklda qaytadan navbatga turadi.
            return False
        oxirgi = _last_try.get(slug)
        if oxirgi is not None and now - oxirgi < RETRY_AFTER:
            return False
        _last_try[slug] = now
        _busy.add(slug)
    threading.Thread(target=_run, args=(slug,), daemon=True,
                     name=f"transport-{slug}").start()
    return True

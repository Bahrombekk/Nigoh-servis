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
# Ulanishga beriladigan qo'shimcha vaqt: o'lchov oynasi real vaqtda
# ketadi, ulanishning o'zi esa sekin kamerada 10 soniyagacha olishi
# mumkin. Usiz sekin kamera "kadr bermadi" bo'lib chiqardi.
CONNECT_ALLOWANCE = float(os.environ.get("TRANSPORT_CONNECT_ALLOWANCE", "12"))
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
# Dekoder kadrni yig'a olmaganini bildiruvchi xabarlar.
# Dekoder kadrni yig'a olmaganini bildiruvchi xabarlar.
#
# DIQQAT: H.264 va H.265 BOSHQA-BOSHQA so'zlar bilan shikoyat qiladi.
# Ilgari bu yerda faqat H.264 naqshlari bor edi va natijada H.265
# kameralarda sinov buzuqlikni UMUMAN KO'RMASDI: UDP "toza" bo'lib
# chiqar, kamera UDP'ga o'tkazilar, tomoshabin esa BUTUNLAY YASHIL
# ekran ko'rardi (shikastlangan HEVC oqimida dekoder bo'sh kadr
# chiqaradi). Ishlab chiqarish jurnalidan olingan haqiqiy xabarlar:
#
#     [hevc] Could not find ref with POC 7
#     [hevc] Skipping invalid undecodable NALU: 39
#     [hevc] The cu_qp_delta -37 is outside the valid range
#     [hevc] Error constructing the frame RPS
_BUZUQ_RE = re.compile(
    # H.264
    r"corrupt decoded frame|error while decoding|cabac decode|"
    r"no frame|non-existing PPS|"
    # H.265
    r"Could not find ref with POC|Skipping invalid undecodable NALU|"
    r"outside the valid range|Error constructing the frame RPS|"
    r"Error parsing NAL unit|"
    # RTP/transport darajasi
    r"invalid fragmentation|missed \d+ packets|RTP: PT=")
_TAKROR_RE = re.compile(r"Last message repeated (\d+) times")


def _frames(url: str, transport: str) -> tuple[int, int]:
    """Shu transport bilan necha SOG'LOM kadr keladi.

    Nima uchun FFmpeg: `core/rtsp_probe.py` faqat RTSP muloqotini
    tekshiradi (DESCRIBE/SETUP), bu nosozlik esa aynan PLAY dan keyin
    boshlanadi — kadrlar oqmaguncha ko'rinmaydi.

    DIQQAT: kadr DEKOD QILINADI (`-c copy` EMAS). Sabab o'lchandi.
    Ilgari sinov kadrni sanardi, butunligini tekshirmasdi — buzuq kadr
    ham "kadr" bo'lib hisoblanardi. Yo'qotishli kanalda UDP doim TCP dan
    ko'p "kadr" beradi (TCP kutadi, UDP kutmaydi), shuning uchun sinov
    bunday kameralarni DOIM UDP'ga o'tkazardi va tomoshabin qotish
    o'rniga buzuq tasvir ko'rardi — yashil bloklar, surilgan kadrlar.

    Shu o'rnatmada o'lchandi (10.30.33.57, bir vaqtda, 30 soniya):

        kameradan to'g'ridan TCP :   0 dekod xatosi
        o'sha kamera UDP orqali  : 147 dekod xatosi

    Qaytaradi: (kelgan kadr, buzuq kadr). Ikkalasi ham kerak — qaror
    faqat songa qarab chiqarilmaydi: buzuq beradigan transport ko'p
    kadr bersa ham yaramaydi (`check` izohiga qarang).
    """
    exe = sync.ffmpeg_path()
    if not exe or not url:
        return -1, 0
    args = [exe, "-hide_banner", "-loglevel", "error", "-stats",
            "-rtsp_transport", transport]
    if transport == "udp":
        args += ["-buffer_size", str(sync.UDP_READ_BUFFER)]
    # DIQQAT: `-t` (VIDEO vaqti) ATAYLAB ishlatilmaydi — o'lchov REAL
    # vaqt bo'yicha ketadi. Sabab o'lchandi: sekin kanalda kamera 8
    # soniyalik videoni 50 soniyada beradi (ffmpeg "speed=0.16x"), ya'ni
    # `-t 8` muddatga ulgurmaydi va jarayon timeout bilan uzilib, sinov
    # -1 qaytaradi. Keyin `_measure` uni "ishonchsiz" deb hisoblardi va
    # SEKINLIK "TCP ishlamaydi" deb talqin qilinardi — kamera esa
    # buzuq tasvir beradigan UDP'ga o'tkazilardi.
    #
    # Real vaqt o'lchovi ikkala transportga ham teng: qaysi biri SHU
    # oynada ko'proq sog'lom kadr bersa, o'sha yaxshi.
    args += ["-i", url, "-an", "-f", "null", "-"]
    try:
        proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                stderr=subprocess.PIPE, text=True)
    except OSError:
        return -1, 0
    bolaklar: list[str] = []

    def oqi() -> None:
        for satr in proc.stderr:
            bolaklar.append(satr)

    oquvchi = threading.Thread(target=oqi, daemon=True)
    oquvchi.start()
    # Ulanishga ham vaqt kerak (o'lchovda sekin kamera 10,5 soniyada
    # ulangan), shuning uchun oyna = ulanish + o'lchov.
    time.sleep(PROBE_SECONDS + CONNECT_ALLOWANCE)
    proc.kill()
    oquvchi.join(timeout=5)
    stderr = "".join(bolaklar)
    found = _FRAME_RE.findall(stderr)
    kadr = int(found[-1]) if found else 0
    return kadr, _buzuq_soni(stderr)


def _buzuq_soni(stderr: str) -> int:
    """Dekoder nechta kadrni yig'a olmaganini sanaydi.

    FFmpeg takrorlanuvchi xabarni siqadi ("Last message repeated N
    times"), shuning uchun ko'paytuvchi ham hisobga olinadi — aks holda
    eng buzuq oqim eng kam xato bergandek ko'rinardi.
    """
    jami = 0
    oldingi_buzuq = False
    for satr in stderr.splitlines():
        takror = _TAKROR_RE.search(satr)
        if takror and oldingi_buzuq:
            jami += int(takror.group(1))
            continue
        oldingi_buzuq = bool(_BUZUQ_RE.search(satr))
        if oldingi_buzuq:
            jami += 1
    return jami


def _stable(url: str, transport: str) -> bool:
    """Shu transport ISHONCHLI ishlaydimi (ketma-ket urinishlarning barchasi).

    Birinchi muvaffaqiyatsizlikda to'xtaydi — sog'lom kamerada ham,
    o'lik kamerada ham ortiqcha ulanish bo'lmasin.
    """
    return _measure(url, transport)[0] > 0


def _measure(url: str, transport: str) -> tuple[int, int]:
    """(eng yomon urinishdagi kadr, urinishlardagi eng ko'p buzuqlik).

    Kadr bo'yicha ENG YOMON urinish olinadi, chunki tomoshabin uchun
    ham o'sha muhim: "goh ishlaydi" — bu ishlamaydi degani. Buzuqlik
    bo'yicha esa eng YOMONI (eng ko'pi) — bir marta buzuq bergan
    transport ishonchli emas.

    (0, buzuq) — ishonchsiz: urinishlarning birortasi kadr bermadi.
    """
    eng_yomon = 0
    eng_buzuq = 0
    for _ in range(STABLE_TRIES):
        kadr, buzuq = _frames(url, transport)
        eng_buzuq = max(eng_buzuq, buzuq)
        if kadr <= MIN_FRAMES:
            return 0, eng_buzuq
        eng_yomon = kadr if not eng_yomon else min(eng_yomon, kadr)
    return eng_yomon, eng_buzuq


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
    hozirgi_kadr, hozirgi_buzuq = _measure(url, hozirgi)
    boshqa_kadr, boshqa_buzuq = _measure(url, boshqa)
    # O'lchov HAR DOIM jurnalga tushadi, qaror o'zgarmagan bo'lsa ham.
    # Aks holda "nega bu kamera ochilmayapti, tekshiruv nima dedi?"
    # degan savolga javob qolmaydi — tashxis jimgina yo'qoladi.
    log("transport", "probe", slug=slug,
        **{hozirgi: hozirgi_kadr, boshqa: boshqa_kadr,
           hozirgi + "_buzuq": hozirgi_buzuq, boshqa + "_buzuq": boshqa_buzuq},
        sekund=PROBE_SECONDS, hozirgi=hozirgi)
    if boshqa_kadr == 0:
        # Ikkinchisi ishonchsiz — o'tishning ma'nosi yo'q. Hozirgisi ham
        # bermayotgan bo'lsa, bu transport muammosi emas (kamera javob
        # bermayapti yoki butun tarmoq nosoz), va tirik transportni o'lik
        # kameraga qarab almashtirish keyin faqat chalkashtiradi.
        return None
    # HOZIRGI transport buzuq, boshqasi toza — NISBATGA QARAMAY o'tamiz.
    #
    # Nima uchun nisbat bu yerda ishlamaydi: u faqat kadr SONINI
    # taqqoslaydi. O'lchovda shunday holat chiqdi —
    #
    #     3395_km_3395_2_km   UDP  90 kadr, 340 buzuq
    #                         TCP 135 kadr,   0 buzuq
    #
    # TCP har jihatdan yaxshi, lekin "3 barobar ko'p" shartiga tushmaydi
    # va kamera buzuq tasvirda qolib ketardi. Buzuq kadr tomoshabin
    # uchun kadr emas, shuning uchun bu yerda son emas, SIFAT hal qiladi.
    if hozirgi_buzuq > boshqa_buzuq and boshqa_kadr > MIN_FRAMES:
        _remember(row, udp=(boshqa == "udp"))
        return boshqa
    # BUZUQLIK — man qiluvchi shart, son bilan qoplanmaydi.
    #
    # O'lchandi (10.30.33.57, bir xil 6 soniyalik video):
    #     TCP : 147 kadr, dekod xatosi 0
    #     UDP : 136 kadr, dekod xatosi 4
    # UDP real vaqtda ko'proq kadr "beradi" (kutmaydi), lekin ularning
    # bir qismi yaroqsiz — ekranda yashil bloklar va surilgan kadrlar.
    # Tomoshabin uchun buzuq kadr kadr emas, shuning uchun toza
    # transportni buzuq transportga almashtirmaymiz.
    if boshqa_buzuq > hozirgi_buzuq:
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

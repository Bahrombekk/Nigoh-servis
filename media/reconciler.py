"""Nigoh — MediaMTX tugunlarini tirik tutuvchi fon vazifasi (media paketi).

Har 30 soniyada, har bir yoqilgan tugun (nodes jadvali) uchun:

  1. Lokal MediaMTX yiqilgan bo'lsa — qayta ishga tushiriladi. Uzoq
     tugundagi jarayonga aralasha olmaymiz — u yiqilsa faqat hodisa
     yoziladi (API javob bermayapti). `MEDIAMTX_AUTOSTART=0` — o'chirish.

  2. Tugun yo'llari kerakli holat bilan kelishtiriladi (`push_to_api`).
     MediaMTX qayta ishga tushganda API orqali qo'shilgan yo'llar
     yo'qoladi — shu yerda o'z-o'zidan tiklanadi. Farq bo'lmasa hech
     narsa yuborilmaydi, ya'ni tinch holatda bu arzon tekshiruv xolos.

  3. Faol oqimlarning bayt hisobi kuzatiladi — STALL_AFTER davomida
     qo'zg'almagan tayyor oqim "muzlagan" deb belgilanadi (hodisa + alert).

Tugundagi MediaMTX BIZNIKI ekani har tsiklda tekshiriladi: begona
o'rnatmaga tegilmaydi (`sync.api_status` izohiga qarang).

Natijada qo'lda aralashish kerak emas: kamera qo'shildi/o'chirildi yoki
MediaMTX yiqildi — 30 soniya ichida tizim o'zini kerakli holatga keltiradi.
"""
import os
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Callable

from core import bus, events, security
from core.db import (
    cameras_by_slug,
    get_db,
    set_sub_bad,
    sub_bad_cameras,
)
from core.log import log
from core.rtsp_probe import build_rtsp_url

from . import sync, transport

CHECK_INTERVAL = 30.0      # soniya — to'liq sinxronlash (yo'llar kelishtiriladi)
# Muzlash tekshiruvi ancha tez-tez: oqim qotganini 60 soniyada bilish
# kuzatuv tizimi uchun juda kech. Faol yo'llar ro'yxati kichik (yo'llar
# talab bo'yicha yaratiladi), shuning uchun bu arzon.
STALL_INTERVAL = 5.0       # soniya — tekshiruv qadami
# Shuncha vaqt bitta bayt kelmasa oqim muzlagan hisoblanadi. Qadamdan
# ancha uzun bo'lishi SHART: kamera ma'lumotni portlash bilan yuborishi
# normal holat (uzun GOP), va ikki portlash orasidagi jimlik muzlash
# emas. 20 soniya — eng sekin kamerada ham ikki-uch keyframe oralig'i,
# lekin kuzatuvchi uchun hali ham tez.
STALL_AFTER = float(os.environ.get("STALL_AFTER", "20"))
# Sub oqim so'ralgan, lekin shuncha vaqt ichida bitta bayt ham kelmasa —
# kamerada ikkinchi oqim yo'q (yoki o'chirilgan) deb belgilanadi.
#
# Amalda uchragan holat: registratorning 8 kanalidan ikkitasida ikkinchi
# oqim yoqilmagan edi (/Streaming/Channels/402 va 802 javob bermasdi),
# asosiy oqimlari esa ishlab turardi. Devorda o'sha ikki katak bo'sh
# qolardi va buni QO'LDA topib, qo'lda belgilash kerak edi.
#
# 45 soniya: `sourceOnDemandStartTimeout` (12 s) dan ancha uzun, ya'ni
# sekin uyg'onadigan kamera noto'g'ri belgilanmaydi.
SUB_DEAD_AFTER = float(os.environ.get("SUB_DEAD_AFTER", "45"))
# Yaroqsiz deb belgilangan sub shuncha vaqtdan keyin qayta sinaladi —
# operator registratorda ikkinchi oqimni yoqsa, tizim buni O'ZI ko'radi
# va belgini oladi. Tekshiruv sub oqimdan kadr o'qib ko'rish bilan.
SUB_RECHECK = float(os.environ.get("SUB_RECHECK", "21600"))   # 6 soat
# Ishga tushgandan keyin birinchi tekshiruvgacha — yangi versiya
# qo'yilganda eski bayroqlar tez tozalansin (yuqoridagi izohga qarang).
SUB_FIRST_RECHECK = float(os.environ.get("SUB_FIRST_RECHECK", "300"))
# Yo'l MediaMTX'da bor, lekin shuncha vaqtdan beri "tayyor" bo'lmadi —
# ya'ni kimdir uni ko'rmoqchi, manba esa ko'tarilmayapti. Shu holatda
# transport tekshiruvi ishga tushadi (media/transport.py): kameralarning
# bir qismi RTSP'ni TCP'da bermaydi va PLAY'dan keyin ulanishni darhol
# yopadi. Muddat manba ochilishidan (SOURCE_START_TIMEOUT, 12 s) va
# relay ko'tarilishidan (30 s) uzun bo'lishi SHART — aks holda sekin
# ochiladigan sog'lom kamera ham tekshiruvga tushadi.
NOT_READY_AFTER = float(os.environ.get("TRANSPORT_CHECK_AFTER", "45"))
SPAWN_COOLDOWN = 30.0      # qayta urinishlar orasidagi eng kam vaqt
STARTUP_WAIT = 8.0         # ishga tushirgandan keyin API'ni shuncha kutamiz

MEDIAMTX_EXE = sync.BASE_DIR / "mediamtx" / (
    "mediamtx.exe" if os.name == "nt" else "mediamtx")
LOG_PATH = sync.DATA_DIR / "mediamtx.log"

_started = False
_lock = threading.Lock()
_process: subprocess.Popen | None = None
_last_spawn = 0.0

# (tugun, yo'l) -> (bytesReceived, shu hisob oxirgi marta o'zgargan vaqt)
_prev_bytes: dict[tuple[int, str], tuple[int, float]] = {}

# Sub oqim salomatligi. `_sub_ok` — shu jarayonda BIR MARTA bo'lsa ham
# kadr bergan sub yo'llar: ular vaqtincha yopilsa ham yaroqsiz deb
# belgilanmaydi (sourceOnDemand yo'lni tomoshabin ketgach yopadi va
# hisob nolga tushadi — bu nosozlik emas).
_sub_ok: set[tuple[int, str]] = set()
# (tugun, yo'l) -> qachondan beri so'ralgan-u, bitta bayt ham kelmagan
_sub_zero: dict[tuple[int, str], float] = {}
# ayni damda RTSP tekshiruvida turgan sub yo'llar — takror tekshirilmasin
_sub_tekshiruvda: set[str] = set()
# Allaqachon "yaroqsiz" deb hukm qilingan sub yo'llar. Ularni jonli
# kuzatuv QAYTA tekshirmaydi — aks holda yo'l MediaMTX'da turgani va
# bo'sh qolgani uchun har tsiklda shubhaga tushib, har daqiqada bitta
# ffprobe ko'tarilardi. Ishlab chiqarishda o'lchandi: bitta kamera
# uchun har ~50 soniyada takror tekshiruv, cheksiz.
#
# Qayta sinash bu yerda emas, `_recheck_sub_bad` da (SUB_RECHECK) —
# o'sha yerda bayroq olinsa, yo'l bu to'plamdan ham chiqadi.
_sub_olik: set[str] = set()
_sub_olik_yuklandi = False
_stalled: dict[tuple[int, str], str] = {}      # (tugun, yo'l) -> ko'rsatma nomi
# (tugun, yo'l) -> oxirgi ko'rilgan buzuq kadrlar hisobi. Yo'l "tayyor"
# bo'lsa ham oqim yaroqsiz bo'lishi mumkin: kamera RTP paketlarni
# tashlab yuboradi, MediaMTX esa "invalid FU-A packet" deb kadrni
# yig'olmaydi va HLS segmenti chiqmaydi — tomoshabin 500 oladi.
# O'lchov: bitta kamera TCP'da sekundiga 1200-1700 paket yo'qotgan,
# o'sha kamera UDP'da bemalol ishlagan.
_errors: dict[tuple[int, str], int] = {}
# (tugun, yo'l) -> (jami NOSOZ vaqt, oxirgi ko'rilgan payt).
#
# Nima uchun JAMI vaqt, "birinchi ko'rilgan payt" emas: ochilmayotgan
# yo'l MediaMTX ro'yxatidan vaqti-vaqti bilan butunlay yo'qoladi
# (manba o'ladi -> talab bo'yicha yo'l o'chadi -> keyingi so'rovda
# qaytadan tug'iladi). Boshlanish payti saqlansa, har yo'qolishda
# hisoblagich noldan boshlanardi va muddat HECH QACHON to'lmasdi —
# o'lchovda 7 daqiqa davomida bitta ham sinov ishga tushmadi.
_not_ready: dict[tuple[int, str], tuple[float, float]] = {}
# Yo'q bo'lib ketgan yo'lning hisobi shuncha vaqt saqlanadi.
_NOT_READY_KEEP = 300.0

# Ortiqcha yo'llar. MediaMTX har `paths/add`/`delete` so'roviga butun
# konfiguratsiyani qayta yuklaydi, ya'ni bitta amal narxi mavjud yo'llar
# soniga chiziqli o'sadi (o'lchov: 0 yo'lda 9 ms, 2400 yo'lda 297 ms).
# Shu sababli tozalash vaqt byudjeti bilan chegaralangan va bir necha
# tsiklga cho'ziladi — shu davrda kamera ochilishi ham sekin bo'ladi.
#
# Eng tez yechim — MediaMTX'ni qayta ishga tushirish: API orqali
# qo'shilgan yo'llar faylga yozilmaydi (tekshirildi: 2051 -> 0), kerakli
# yo'l esa ko'rish so'ralganda o'zi qaytadan yaratiladi. Buni avtomatik
# qilmaymiz — tirik oqimlarni uzib yuborardi; operatorga aytamiz.
BLOAT_WARN_EVERY = 300.0                       # soniya
_pending: dict[int, int] = {}                  # tugun -> tozalanmagan yo'llar
_bloat_warned: dict[int, float] = {}

# Begona MediaMTX (boshqa o'rnatmaniki) — `sync.api_status` izohiga qarang.
FOREIGN_WARN_EVERY = 300.0                     # soniya
_foreign: dict[int, float] = {}                # tugun -> oxirgi ko'rilgan vaqt
_foreign_warned: dict[int, float] = {}

# Oxirgi to'liq sinxronda API'si javob bergan tugunlar. Tez tsikl faqat
# shularni tekshiradi — o'lik tugunning timeout'i tsiklni cho'zmasin.
_reachable: set[int] = set()


def stalled_paths() -> set[str]:
    """Ayni damda muzlagan (bayt kelmayotgan) faol yo'llar."""
    with _lock:
        return set(_stalled.values())


def stalled_count(node_id: int) -> int:
    """Bitta tugundagi muzlagan oqimlar soni — tugun salomatligi uchun."""
    with _lock:
        return sum(1 for key in _stalled if key[0] == node_id)


def pending_count(node_id: int) -> int:
    """Tugunda hali tozalanmagan ortiqcha yo'llar — 0 bo'lishi kerak."""
    with _lock:
        return _pending.get(node_id, 0)


def _note_pending(node: dict, pending: int) -> None:
    """Tozalanmagan yo'llarni qayd etadi va operatorni ogohlantiradi."""
    with _lock:
        _pending[node["id"]] = pending
    if not pending:
        return
    now = time.monotonic()
    with _lock:
        if now - _bloat_warned.get(node["id"], 0.0) < BLOAT_WARN_EVERY:
            return
        _bloat_warned[node["id"]] = now
    log("reconciler", "paths_bloated", level="warning", node=node["name"],
        pending=pending,
        message="MediaMTX'da ortiqcha yo'llar ko'p — tozalanmoqda, shu "
                "davrda kamera sekinroq ochiladi. Tezroq yo'l: MediaMTX'ni "
                "qayta ishga tushiring (yo'llar faylga yozilmaydi, "
                "kerakligi ko'rilganda o'zi tiklanadi).")


def _warn_foreign(node: dict, api: str) -> None:
    """Tugun begona MediaMTX'ga qarab turibdi — operatorni ogohlantiradi.

    Bu jimgina o'tkazib yuboriladigan holat emas: shu mashinada ikkinchi
    Nigoh o'rnatmasi ishga tushsa (yoki eski nusxaning `ishga-tushirish`
    fayli bosilsa) ikkala backend bitta 9997-portga qaraydi va bir-birining
    yo'llarini o'chirib turadi. Tashqaridan bu "kameralar uziladi, qotib
    qoladi" bo'lib ko'rinadi va sababini topish qiyin.
    """
    now = time.monotonic()
    with _lock:
        _foreign[node["id"]] = now
        if now - _foreign_warned.get(node["id"], 0.0) < FOREIGN_WARN_EVERY:
            return
        _foreign_warned[node["id"]] = now
    message = sync._foreign_message(api)
    log("reconciler", "mediamtx_begona", level="error",
        node=node["name"], message=message)
    try:
        with get_db() as db:
            events.add(db, "mediamtx", detail=message)
    except Exception:
        pass


def foreign_nodes() -> int:
    """Begona MediaMTX'ga qarab turgan tugunlar soni — /health uchun."""
    cutoff = time.monotonic() - 2 * CHECK_INTERVAL
    with _lock:
        return sum(1 for t in _foreign.values() if t > cutoff)


def _nodes() -> list[dict]:
    """Yoqilgan MediaMTX tugunlari; jadval bo'sh bo'lsa — lokal standart."""
    try:
        with get_db() as db:
            rows = db.execute("SELECT * FROM nodes WHERE enabled = 1").fetchall()
        nodes = [dict(r) for r in rows]
    except Exception:
        nodes = []
    return nodes or [{"id": 1, "name": "Asosiy", "api_base": sync.API_BASE}]


def _autostart_allowed() -> bool:
    if os.environ.get("MEDIAMTX_AUTOSTART", "1") == "0":
        return False
    return MEDIAMTX_EXE.exists()


def _spawn() -> bool:
    """Lokal MediaMTX'ni ishga tushiradi; log ildizdagi mediamtx.log da."""
    global _process, _last_spawn
    now = time.monotonic()
    if now - _last_spawn < SPAWN_COOLDOWN:
        return False
    if _process is not None and _process.poll() is None:
        return False               # biz ochgan jarayon tirik — hali ko'tarilyapti
    _last_spawn = now
    try:
        log_file = open(LOG_PATH, "ab")
        _process = subprocess.Popen(
            [str(MEDIAMTX_EXE), str(sync.CONFIG_PATH)],
            cwd=str(sync.BASE_DIR),
            stdout=log_file, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
        )
    except OSError as exc:
        log("reconciler", "mediamtx_spawn_failed", level="error", error=str(exc))
        return False
    log("reconciler", "mediamtx_restarted", log_file=LOG_PATH.name)
    try:
        with get_db() as db:
            events.add(db, "mediamtx", detail="MediaMTX qayta ishga tushirildi")
    except Exception:
        pass
    # API ko'tarilishini qisqa kutamiz — yo'llar shu tickning o'zida tiklansin.
    deadline = time.monotonic() + STARTUP_WAIT
    while time.monotonic() < deadline:
        if sync.api_available():
            return True
        if _process.poll() is not None:
            _log_spawn_death()
            return False
        time.sleep(0.5)
    return False


DEATH_WARN_EVERY = 300.0     # soniya — jurnal toshib ketmasin
_death_warned = [0.0]


def _log_spawn_death() -> None:
    """MediaMTX ko'tarilmasdan o'ldi — SABABINI jurnalga chiqaradi.

    Ilgari bu jimgina o'tardi: reconciler har tsiklda `mediamtx_restarted`
    yozib qayta urinardi, sabab esa faqat `mediamtx.log` da qolardi va
    hech kim u yerga qaramasdi. Amalda bitta port to'qnashuvi (`listen udp
    :8189: bind: ...` — qo'shni o'rnatma bilan bo'lishilgan ICE porti)
    butun video xizmatini o'ldirgan, tashqaridan esa "kameralar
    ishlamayapti" bo'lib ko'ringan.
    """
    now = time.monotonic()
    with _lock:
        if now - _death_warned[0] < DEATH_WARN_EVERY:
            return
        _death_warned[0] = now
    sabab = ""
    try:
        # Faqat oxiri o'qiladi: mediamtx.log megabaytlarga o'sishi mumkin,
        # bizga esa o'lishdan oldingi bir necha satr yetadi.
        with open(LOG_PATH, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - 8192))
            oxiri = f.read().decode("utf-8", errors="replace")
        sabab = next((s for s in reversed(oxiri.splitlines()) if " ERR " in s), "")
    except OSError:
        pass
    log("reconciler", "mediamtx_kotarilmadi", level="error",
        code=_process.returncode if _process else None,
        error=sabab or f"sabab {LOG_PATH.name} da",
        message="MediaMTX ishga tushmadi. Eng ko'p uchraydigan sabab — port "
                "band (shu mashinada ikkinchi o'rnatma). .env dagi "
                "MEDIAMTX_API, MEDIAMTX_RTSP_PORT, HLS_PORT, WEBRTC_PORT "
                "va WEBRTC_UDP_PORT/WEBRTC_TCP_PORT ni bo'sh portlarga o'zgartiring")


def _sub_belgila(slugs: list[str], bad: bool) -> None:
    """`sub_bad` bayrog'ini bazaga yozadi va hodisa qoldiradi.

    Faqat HAQIQATAN o'zgargan qatorga yoziladi (`AND sub_bad = ?`),
    shuning uchun har tsiklda takror yozuv ham, takror jurnal ham
    bo'lmaydi.
    """
    kameralar = [s[: -len(sync.SUB_SUFFIX)] for s in slugs]
    for kamera in set_sub_bad(kameralar, bad):
        log("reconciler", "sub_yaroqsiz" if bad else "sub_tiklandi",
            level="warning" if bad else "info", camera=kamera,
            sabab=("sub oqim so'raldi-yu kelmadi, tekshiruvda ham kadr "
                   "bermadi — registratorda ikkinchi oqim yo'q yoki "
                   "o'chirilgan; endi asosiy oqim beriladi")
            if bad else "sub oqim yana kadr beryapti — asosiyga o'tish bekor")


def _check_sub_health(node: dict, active: dict[str, dict]) -> None:
    """Sub oqim so'ralgan-u kelmasa — kamerani `sub_bad` deb belgilaydi.

    Nima uchun serverda: pleyer ham buni aniqlay oladi, lekin faqat
    O'SHA tomoshabin uchun va faqat u ochib ko'rgandan keyin. Server bir
    marta ko'rsa — hamma mijoz uchun, qayta yuklangandan keyin ham
    o'rinli bo'ladi va hech kim qo'lda aralashmaydi.

    Shartlar ataylab qattiq — sog'lom kamerani noto'g'ri belgilash
    tomoshani og'irlashtiradi (asosiy oqim o'lchovda 7,88 Mbit/s, sub
    1,20 Mbit/s edi):

      * yo'l ISSIQ bo'lishi kerak (`is_warm`) — ya'ni kimdir haqiqatan
        so'ragan. So'ralmagan yo'l tayyor emasligi normal holat;
      * bitta ham bayt kelmagan bo'lishi kerak;
      * shu holat SUB_DEAD_AFTER davom etishi kerak;
      * yo'l ilgari BIR MARTA ishlagan bo'lsa (`_sub_ok`) hech qachon
        belgilanmaydi — tomoshabin ketgach sourceOnDemand yo'lni yopadi
        va hisob nolga tushadi, bu nosozlikka o'xshab ko'rinadi.

    DIQQAT: bu funksiya HUKM CHIQARMAYDI, faqat shubha uyg'otadi. Nima
    uchun — jonli tizimda sinaganda soxta ishga tushdi: sog'lom kanalning
    sub yo'li "issiq" edi (manzil so'ralgan), lekin tomoshabin ulanmagani
    uchun yo'l bo'sh turardi va u yaroqsiz deb belgilanardi. "So'raldi"
    degani "tortib ko'rildi" degani emas. Shuning uchun yakuniy qarorni
    `_sub_tasdiqla` RTSP tekshiruvi bilan chiqaradi.
    """
    global _sub_olik_yuklandi
    node_id = node["id"]
    now = time.monotonic()
    shubhali: list[str] = []
    tirik: list[str] = []
    if not _sub_olik_yuklandi:
        # Bazadagi bayroqlar jarayon qayta ishga tushganda ham o'rinli:
        # usiz har restartdan keyin hamma belgilangan kamera qaytadan
        # tekshirilardi. Qayta sinashni `_recheck_sub_bad` bajaradi.
        try:
            with _lock:
                _sub_olik.update(c["slug"] + sync.SUB_SUFFIX
                                 for c in sub_bad_cameras())
            _sub_olik_yuklandi = True
        except Exception:      # baza hali tayyor bo'lmasa keyingi tsiklda
            pass
    with _lock:
        for name, item in active.items():
            # `_sub_h264` — o'girish CHIQISHI, kameraning oqimi emas.
            if not name.endswith(sync.SUB_SUFFIX):
                continue
            key = (node_id, name)
            got = int(item.get("bytesReceived") or 0)
            if item.get("ready") and got > 0:
                _sub_ok.add(key)
                _sub_zero.pop(key, None)
                _sub_olik.discard(name)
                tirik.append(name)
                continue
            if key in _sub_ok or got > 0 or name in _sub_olik:
                continue
            if not sync.is_warm(name):
                _sub_zero.pop(key, None)
                continue
            # Transport sinovi ketayotgan kamerani hozir baholamaymiz:
            # sinov davomida kamera TCP'da kadr bermasligi normal holat
            # (u aynan shuni o'lchayapti), va sinov kamerani UDP'ga
            # o'tkazsa sub o'z-o'zidan ishlab ketishi mumkin. `_sub_zero`
            # tozalanmaydi — keyingi tsiklda qaytadan navbatga turadi.
            if transport.busy(name[: -len(sync.SUB_SUFFIX)]):
                continue
            birinchi = _sub_zero.setdefault(key, now)
            if now - birinchi >= SUB_DEAD_AFTER and name not in _sub_tekshiruvda:
                _sub_zero.pop(key, None)
                _sub_tekshiruvda.add(name)
                shubhali.append(name)
        for key in [k for k in _sub_zero
                    if k[0] == node_id and k[1] not in active]:
            _sub_zero.pop(key)
    if tirik:
        _sub_belgila(tirik, bad=False)
    if shubhali:
        # Qarorni kuzatuv EMAS, tekshiruv chiqaradi — pastdagi izohga qarang.
        threading.Thread(target=_sub_tasdiqla, args=(shubhali,),
                         daemon=True).start()


def _sub_kadr_beradimi(cam: dict) -> bool:
    """Kameraning sub oqimidan haqiqatan kadr keladimi.

    DIQQAT: RTSP DESCHRIBE bilan tekshirish YETARLI EMAS va bu shu
    o'rnatmada o'lchandi — registratorning 4 va 8-kanali DESCRIBE'ga
    javob berib, SDP'da sub oqimni e'lon qilardi, lekin bitta ham paket
    bermasdi. `core.rtsp_probe.probe` ikkalasini "sog'lom" deb
    ko'rsatgan, bazadagi `sub_codec` ham shundan H265 bo'lib qolgan.
    Shuning uchun tekshiruv paket darajasida.
    """
    try:
        url = build_rtsp_url(cam["ip"], cam["port"] or 554, cam["sub_path"],
                             cam["username"] or "",
                             security.decrypt(cam["password_enc"]))
        return sync.kadr_keladimi(url, udp=bool(cam.get("rtsp_udp")))
    except Exception:              # bitta kamera qolganini uzmasin
        return True                # shubhada ayblamaymiz


def _sub_tasdiqla(slugs: list[str]) -> None:
    """Shubhali sub yo'llarni RTSP DESCRIBE bilan tekshirib hukm chiqaradi.

    Kuzatuv "so'raldi-yu kelmadi" deyishi mumkin, lekin buning aybsiz
    sababi ham bor (tomoshabin ulanmay yopib qo'ydi). Tekshiruv esa
    kameraning o'zidan so'raydi: ikkinchi oqim BORMI. Faqat u javob
    bermasa kamera `sub_bad` bo'ladi.

    Alohida oqimda: probe sekin kamerada bir necha soniya kutadi,
    reconciler tsikli esa (u bilan birga muzlash kuzatuvi) turib
    qolmasligi kerak.
    """
    kameralar = [s[: -len(sync.SUB_SUFFIX)] for s in slugs]
    try:
        olik: list[str] = []
        for c in cameras_by_slug(kameralar):
            if _sub_kadr_beradimi(c):
                continue
            olik.append(c["slug"])
            log("reconciler", "sub_tekshiruv", level="info", camera=c["slug"],
                xabar="sub oqimdan kadr kelmadi")
        if olik:
            olik_yollar = [s + sync.SUB_SUFFIX for s in olik]
            with _lock:
                _sub_olik.update(olik_yollar)
            _sub_belgila(olik_yollar, bad=True)
    finally:
        with _lock:
            _sub_tekshiruvda.difference_update(slugs)


def _recheck_sub_bad() -> None:
    """Yaroqsiz deb belgilangan sub oqimlarni qayta sinab ko'radi.

    Busiz bayroq abadiy qolardi: `sub_bad` qo'yilgach mijoz sub'ni
    boshqa so'ramaydi, ya'ni yo'l yaratilmaydi va jonli kuzatuv uni
    hech qachon "tuzalgan" deb ko'ra olmaydi. Operator registratorda
    ikkinchi oqimni yoqsa ham, kimdir QO'LDA bayroqni olishi kerak
    bo'lardi — aynan shu qo'l mehnatidan qutulmoqchimiz.

    Tekshiruv sub oqimdan kadr o'qib ko'rish bilan — DESCRIBE yetarli
    emas (`_sub_kadr_beradimi` izohiga qarang). Alohida oqimda ishlaydi:
    sekin javob beradigan kameralar tsiklni (va u bilan birga muzlash
    kuzatuvini) ushlab qolmasin.
    """
    kameralar = sub_bad_cameras()
    if not kameralar:
        return
    tuzalgan = [c["slug"] for c in kameralar if _sub_kadr_beradimi(c)]
    with _lock:
        _sub_olik.difference_update(s + sync.SUB_SUFFIX for s in tuzalgan)
    for slug in set_sub_bad(tuzalgan, bad=False):
        log("reconciler", "sub_tiklandi", camera=slug,
            sabab="qayta tekshiruvda sub oqim javob berdi — endi yana "
                  "devorda sub ishlatiladi")


def _sinov_buyur(node_id: int, path: str) -> None:
    """Ochilmayotgan yo'l uchun transport sinovini fonga buyuradi.

    Faqat 1-tugun (backend bilan bitta mashinada): sinov kameraga SHU
    mashinadan ulanadi, uzoq tugundagi kamera esa boshqa tarmoqda —
    bu yerdan o'lchov yolg'on chiqadi.

    `wall_` (mozaika) yo'llari kamera emas, ularda transport degan
    tushuncha yo'q. `_h264` va `_sub` — o'sha kameraning ko'rinishlari,
    shuning uchun asosiy slug bo'yicha sinaladi (transport butun
    qurilmaga tegishli, alohida oqimga emas).
    """
    if node_id != 1 or path.startswith("wall_"):
        return
    slug = path
    for suffix in (sync.TRANSCODE_SUFFIX, sync.SUB_SUFFIX):
        if slug.endswith(suffix):
            slug = slug[: -len(suffix)]
    transport.request(slug)


def _check_stalls(node: dict) -> None:
    """Bayt hisobi STALL_AFTER davomida qo'zg'almasa — oqim muzlagan.

    TCP tekshiruv (health) buni ko'rmaydi: registrator portga javob
    beraveradi, lekin kanal tasvir bermay qolishi mumkin. bytesReceived
    esa yolg'on gapirmaydi.

    O'lchov vaqt bo'yicha, TSIKL bo'yicha emas. Ilgari ketma-ket ikki
    tsikl (5 s) taqqoslanardi va bu soxta signal mashinasi edi: uzun
    GOP'li yoki kam tezlikdagi kamera ma'lumotni portlash bilan yuboradi
    (o'lchov: bitta Dahua kanali har 6-8 soniyada ~675 KB, orada nol), va
    har portlash orasida yo'l "muzladi -> tiklandi" bo'lib jurnalga
    tushardi — 5 soniyalik "muzlash" 12 marta ketma-ket. Tomoshabinga
    SSE orqali `stalled` yuborilardi, ya'ni ishlab turgan kamera
    muammoli bo'lib ko'rinardi.
    """
    node_id = node["id"]
    active = sync.list_active_paths(node["api_base"])
    if active is None:
        return
    now = time.monotonic()
    changes: list[tuple[str, str, str]] = []     # (ko'rsatma, yo'l, holat)
    tekshirilsin: list[str] = []                 # transport sinoviga nomzodlar
    with _lock:
        for name, item in active.items():
            key = (node_id, name)
            got = int(item.get("bytesReceived") or 0)
            prev = _prev_bytes.get(key)
            # "Tayyor emas" holati qancha cho'zilgani — bayt hisobidan
            # MUSTAQIL kuzatiladi. Manba ko'tarilib darhol o'lsa (kamera
            # TCP'ni ko'tarmasa) yo'l hech qachon tayyor bo'lmaydi, bayt
            # esa har urinishda noldan boshlanadi — ya'ni quyidagi
            # muzlash mantig'i buni umuman ko'rmaydi.
            # Nosozlikning ikki ko'rinishi bir xil hisoblanadi: yo'l
            # umuman tayyor bo'lmasligi ham, tayyor bo'lib buzuq kadr
            # berishi ham tomoshabin uchun bitta natija — video yo'q.
            xato = int(item.get("inboundFramesInError") or 0)
            oldingi = _errors.get(key)
            _errors[key] = xato
            nosoz = (not item.get("ready")) or (oldingi is not None and xato > oldingi)
            if not nosoz:
                _not_ready.pop(key, None)
            else:
                jami, oxirgi = _not_ready.get(key, (0.0, now))
                # Faqat UZLUKSIZ kuzatuv qo'shiladi: yo'l bir necha
                # tsikl ko'rinmay tursa, o'sha oraliq hisobga kirmaydi.
                if now - oxirgi <= STALL_INTERVAL * 3:
                    jami += now - oxirgi
                _not_ready[key] = (jami, now)
                if jami >= NOT_READY_AFTER:
                    tekshirilsin.append(name)
            # Bayt keldi (yoki yo'lni birinchi marta ko'ryapmiz) — hisob
            # noldan boshlanadi. Faqat shu yerda vaqt yangilanadi:
            # o'zgarmagan tsiklda yangilansa muddat hech qachon to'lmasdi.
            if prev is None or got != prev[0]:
                _prev_bytes[key] = (got, now)
                if key in _stalled:
                    changes.append((_stalled.pop(key), name, "resumed"))
                continue
            if not item.get("ready"):
                # Hali ulanmoqda — bu muzlash emas va hisob ham YURMASIN.
                # Aks holda sekin ochiladigan kamera (o'lchov: bittasida
                # relay 15 s da ulangan) tayyor bo'lgan zahoti "muzlagan"
                # deb belgilanardi: soat u ulanayotgan paytda ishlab
                # bo'lgan bo'lardi.
                _prev_bytes[key] = (got, now)
                continue
            if now - prev[1] >= STALL_AFTER and key not in _stalled:
                display = name if node_id == 1 else f"{name}@{node['name']}"
                _stalled[key] = display
                changes.append((display, name, "stalled"))
        for key in list(_stalled):
            if key[0] == node_id and key[1] not in active:
                _stalled.pop(key)                 # oqim yopildi — muzlash tugadi
        for key in [k for k in _prev_bytes if k[0] == node_id and k[1] not in active]:
            _prev_bytes.pop(key)
        # Yo'qolgan yo'lning hisobi DARHOL o'chirilmaydi (yuqoridagi
        # izoh) — faqat ancha vaqt ko'rinmagani tashlanadi.
        for key, (_, oxirgi) in list(_not_ready.items()):
            if key[0] == node_id and now - oxirgi > _NOT_READY_KEEP:
                _not_ready.pop(key, None)
                _errors.pop(key, None)
        for key in [k for k in _errors if k[0] == node_id and k[1] not in active]:
            _errors.pop(key, None)
    # Ro'yxat allaqachon qo'lda — ikkinchi API so'rovi shart emas.
    _check_sub_health(node, active)
    # Qulfdan TASHQARIDA: sinovning o'zi fon thread'ida ketadi, lekin
    # navbatga qo'yish ham reconciler qulfini ushlab turmasin.
    for name in tekshirilsin:
        _sinov_buyur(node_id, name)
    if not changes:
        return
    at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with get_db() as db:
        for display, name, kind in changes:
            events.add(db, kind, slug=display,
                       detail="oqim muzladi" if kind == "stalled" else "oqim tiklandi")
            log("reconciler", f"stream_{kind}",
                level="warning" if kind == "stalled" else "info", path=display)

            # SSE: yo'l nomidan kamera topiladi (suffikslar olib tashlanadi).
            # `resumed` tashqariga `online` bo'lib chiqadi — abonent uchun
            # holat lug'ati bitta: online/offline/stalled.
            #
            # SUB oqim muzlashi kamera holatini o'zgartirmaydi: sub faqat
            # devor kataklari uchun, asosiy oqim ishlab tursa kamera muammoli
            # emas (camera_state ham shunday hisoblaydi). Hodisa jurnalda
            # qoladi (yuqorida yozildi), lekin tomoshabinga stalled yuborilmaydi.
            if name.endswith(sync.SUB_SUFFIX):
                continue
            base = name
            for suffix in (sync.TRANSCODE_SUFFIX, sync.SUB_SUFFIX):
                if base.endswith(suffix):
                    base = base[: -len(suffix)]
            row = db.execute("SELECT id, external_id FROM cameras WHERE slug = ?",
                             (base,)).fetchone()
            if row:
                bus.publish("state", {
                    "id": row["id"],
                    "external_id": row["external_id"] or "",
                    "state": "stalled" if kind == "stalled" else "online",
                    "at": at,
                })


def _tick(load_cameras: Callable[[], list[dict]], announce: bool) -> bool:
    """Bitta tekshiruv (barcha tugunlar). Sinxron bajarilsa True qaytaradi."""
    cameras = load_cameras()
    synced = False
    for node in _nodes():
        api = node["api_base"]
        local = sync.is_local_api(api)
        status = sync.api_status(api)
        if status == sync.FOREIGN:
            # Begona instansiya: kelishtirmaymiz HAM, o'zimiznikini
            # ko'tarmaymiz ham. Ko'targanda ham foyda yo'q — API porti
            # band, yangi jarayon darhol o'lardi va biz uni har tsiklda
            # qayta urintirardik.
            _warn_foreign(node, api)
            with _lock:
                _reachable.discard(node["id"])
            continue
        if status != "ok":
            if not (local and _autostart_allowed() and _spawn()):
                with _lock:
                    _reachable.discard(node["id"])
                continue
        with _lock:
            _reachable.add(node["id"])
        node_cams = [c for c in cameras
                     if (c.get("node_id") or 1) == node["id"]]
        result = sync.push_to_api(node_cams, api_base=api)
        _note_pending(node, result.get("pending", 0))
        changed = result["added"] + result["updated"] + result["removed"]
        if announce or changed or not result["ok"]:
            log("reconciler", "sync",
                level="info" if result["ok"] else "warning",
                node=node["name"], added=result["added"],
                updated=result["updated"], removed=result["removed"],
                message=result["message"])
        synced = synced or result["ok"]
    return synced


def _watch_active() -> None:
    """Faqat muzlash tekshiruvi — to'liq sinxronsiz, tez tsikl uchun.

    Faol yo'llar ro'yxati kichik (yo'llar talab bo'yicha yaratilgani
    uchun MediaMTX'da faqat ko'rilayotganlari turadi), shuning uchun buni
    5000 kamerada ham 5 soniyada bir chaqirish arzon.

    Javob bermayotgan tugun o'tkazib yuboriladi: aks holda har tsikl
    uning timeout'ini (4 s) kutib o'tirardi. Uni to'liq sinxron
    (`_tick`) qayta sinaydi.
    """
    with _lock:
        alive = set(_reachable)
    for node in _nodes():
        if node["id"] in alive:
            _check_stalls(node)


PRUNE_INTERVAL = 3600.0    # soniya — eski hodisalar soatiga bir tozalanadi


def _loop(load_cameras: Callable[[], list[dict]]) -> None:
    announced = False              # birinchi muvaffaqiyatli sinxron logda ko'rinsin
    last_prune = 0.0
    last_sync = 0.0
    # Birinchi tekshiruv darhol emas: ishga tushishda kameralar hali
    # ulanmagan bo'lishi mumkin va hammasi "tuzalmagan" bo'lib chiqardi.
    #
    # Lekin TO'LIQ muddat (6 soat) ham uzoq: yangi versiya qo'yilganda
    # bazada eski, NOTO'G'RI `sub_bad` bayroqlari qolgan bo'lishi mumkin
    # (ilgari pleyer har qotishda belgilardi) va ular devorni og'ir
    # asosiy oqimga o'tkazib turadi. Shu o'rnatmada o'lchandi: 32 ta
    # belgilangan kameradan 29 tasi tekshiruvda SOG'LOM chiqdi.
    # Shuning uchun birinchi tekshiruv ishga tushgandan ko'p o'tmay.
    last_sub_recheck = time.monotonic() - SUB_RECHECK + SUB_FIRST_RECHECK
    while True:
        try:
            now = time.monotonic()
            if not last_sync or now - last_sync >= CHECK_INTERVAL:
                last_sync = now
                if _tick(load_cameras, not announced):
                    announced = True
                if now - last_prune > PRUNE_INTERVAL:
                    last_prune = now
                    with get_db() as db:
                        events.prune(db)
                if now - last_sub_recheck >= SUB_RECHECK:
                    last_sub_recheck = now
                    threading.Thread(target=_recheck_sub_bad,
                                     daemon=True).start()
            # Muzlash tekshiruvi har tsiklda — to'liq sinxrondan ancha
            # tez-tez. Tomoshabin bor oqim qotganini 60 soniyada emas,
            # 5-10 soniyada bilamiz.
            _watch_active()
        except Exception as exc:   # kuzatuv hech qachon yiqilmasin
            log("reconciler", "tick_failed", level="error", error=str(exc))
        time.sleep(STALL_INTERVAL)


def start(load_cameras: Callable[[], list[dict]]) -> None:
    """Fon reconcilerini ishga tushiradi (bir marta).

    `load_cameras` — kameralarning MediaMTX ko'rinishini qaytaruvchi
    funksiya; uni app qatlami uzatadi (media qatlami bazaga sxema
    darajasida bog'lanmasin).
    """
    global _started
    with _lock:
        if _started:
            return
        _started = True
    threading.Thread(target=_loop, args=(load_cameras,), daemon=True).start()

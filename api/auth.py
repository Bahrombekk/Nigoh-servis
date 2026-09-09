"""Nigoh — autentifikatsiya endpointlari."""
import ipaddress
import math
import os
import threading
import time
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request, Response

from core import security
from core.db import get_db
from core.log import log

from .models import LoginIn

# Prefiks nisbiy — create_app uni /api/v1 (asosiy) va /api (eski) ostida ulaydi.
#
# Ikkita router: `router` (/auth/stream) HAR DOIM ulanadi — uni MediaMTX
# chaqiradi, usiz birorta oqim ochilmaydi. `ui_router` (login/logout/me)
# faqat ENABLE_UI=1 bo'lganda ulanadi — ishlab chiqarishda login yuzasi
# umuman yo'q.
router = APIRouter(prefix="/auth", tags=["auth"])
ui_router = APIRouter(prefix="/auth", tags=["auth"])


# ---------- login brute-force himoyasi ----------
#
# IP bo'yicha eksponensial KUTISH MUDDATI: dastlabki 5 xato jazosiz
# (barmoq xatosi uchun), keyin har xato keyingi urinishgacha bo'lgan
# muddatni ikki baravar oshiradi (1s, 2s, 4s ... eng ko'pi 30s). Muddat
# tugamasdan kelgan so'rov 429 bilan qaytariladi — parol umuman
# tekshirilmaydi. Har xato jurnalga `login_failed` bo'lib yoziladi —
# fail2ban shu satr bo'yicha ip'ni butunlay bloklashi mumkin.
#
# Nima uchun uxlash EMAS: ilgari bu yerda `time.sleep(delay)` turardi va
# izohda "sync endpoint threadpool'da — boshqalarni bloklamaydi" deb
# yozilgandi. Bu noto'g'ri: Starlette'ning threadpool'i 40 ta ip va uni
# barcha `def` endpointlar bo'lishadi (bu servisda deyarli hammasi).
# 40 ta parallel xato kirish har biri 30 s uxlab pool'ni to'ldirardi va
# kameralar ro'yxati ham, admin ham javob bermay qolardi. Kutish
# muddatini mijozga aytish bir xil himoyani beradi, lekin serverda
# birorta resurs egallamaydi.

_FAIL_FREE = 5           # shu songacha kutish yo'q
_FAIL_MAX_DELAY = 30.0   # soniya
_FAIL_TTL = 3600.0       # soniya — shuncha tinch turgan ip hisobi unutiladi
_fails: dict[str, tuple[int, float]] = {}    # ip -> (xato soni, oxirgi vaqt)
_fails_lock = threading.Lock()

# `X-Forwarded-For` faqat ishonchli proksidan kelganda hisobga olinadi.
# Aks holda cheklovni aylanib o'tish arzon: har so'rovda boshqa soxta
# sarlavha yuborilsa har safar yangi "ip" hisobi ochiladi va eksponensial
# kutish umuman ishlamaydi. MediaMTX konfiguratsiyasida shu tamoyil
# allaqachon bor (`hlsTrustedProxies`), bu yerda yetishmasdi.
#
# Standart — loopback va ichki tarmoqlar: nginx shu mashinada turadi
# (`network_mode: host`, proksi 127.0.0.1 ga), lekin uni alohida hostga
# ko'chirsa ham sozlamasiz ishlayversin. Internetdan to'g'ridan kelgan
# so'rovning sarlavhasiga hech qachon ishonilmaydi. `TRUSTED_PROXIES`
# (vergul bilan) berilsa — faqat o'sha manzillar.
_TRUSTED_ENV = os.environ.get("TRUSTED_PROXIES", "").strip()
TRUSTED_PROXIES = {p.strip() for p in _TRUSTED_ENV.split(",") if p.strip()}


def _ishonchli_proksi(peer: str) -> bool:
    if TRUSTED_PROXIES:
        return peer in TRUSTED_PROXIES
    try:
        manzil = ipaddress.ip_address(peer)
    except ValueError:
        return False
    return manzil.is_loopback or manzil.is_private


def _client_ip(request: Request) -> str:
    """So'rov kelgan haqiqiy manzil.

    Nginx ortida u `X-Forwarded-For` da bo'ladi, lekin sarlavhaga faqat
    ulanish IShONChLI proksidan kelgandagina ishonamiz — aks holda uni
    har kim o'zi yozib yuboradi.
    """
    peer = request.client.host if request.client else ""
    if _ishonchli_proksi(peer):
        fwd = request.headers.get("x-forwarded-for", "")
        if fwd:
            return fwd.split(",")[0].strip()
    return peer


def _https_dami(request: Request) -> bool:
    """So'rov tomoshabingacha HTTPS bo'lganmi.

    Nginx ortida ilova o'zi http'da tinglaydi, shuning uchun sxema
    `X-Forwarded-Proto` dan olinadi — va u ham faqat ishonchli proksidan
    hisobga olinadi (soxta sarlavha cookie'ni noto'g'ri belgilamasin).
    """
    peer = request.client.host if request.client else ""
    if _ishonchli_proksi(peer):
        proto = request.headers.get("x-forwarded-proto", "").split(",")[0].strip()
        if proto:
            return proto.lower() == "https"
    return request.url.scheme == "https"


def _retry_after(ip: str) -> float:
    """Shu ip yana urinishi uchun necha soniya qolgani (0 — hoziroq mumkin)."""
    now = time.monotonic()
    with _fails_lock:
        if len(_fails) > 1000:               # xotira cheksiz o'smasin
            for k, (_, t) in list(_fails.items()):
                if now - t > _FAIL_TTL:
                    _fails.pop(k, None)
        count, last = _fails.get(ip, (0, 0.0))
        if now - last > _FAIL_TTL or count < _FAIL_FREE:
            return 0.0
        wait = min(2.0 ** (count - _FAIL_FREE), _FAIL_MAX_DELAY)
        return max(0.0, last + wait - now)


def _note_fail(ip: str) -> None:
    now = time.monotonic()
    with _fails_lock:
        count, last = _fails.get(ip, (0, 0.0))
        if now - last > _FAIL_TTL:
            count = 0
        _fails[ip] = (count + 1, now)


def _clear_fails(ip: str) -> None:
    with _fails_lock:
        _fails.pop(ip, None)


@router.post("/stream")
def stream_auth(body: dict):
    """MediaMTX har bir ulanishda shu yerdan ruxsat so'raydi (authMethod: http).

    Buni brauzer emas, MediaMTX'ning o'zi chaqiradi: 200 — ruxsat,
    401 — rad. Shu bilan 8554/8888/8889-portlardagi oqimlarni saytdan
    berilgan chiptasiz ko'rib bo'lmaydi.
    """
    ip = str(body.get("ip") or "")
    action = str(body.get("action") or "")
    path = str(body.get("path") or "")
    query = str(body.get("query") or "")
    token = (parse_qs(query).get("token") or [""])[0]

    # O'z jarayonlarimiz (launcher FFmpeg'i, snapshot zaxirasi) ichki
    # chipta bilan yuradi — publish ham, read ham mumkin. IP'ga qarab
    # ishonib bo'lmaydi: nginx proksi ortida barcha tomoshabin 127.0.0.1
    # bo'lib ko'rinadi, IP istisno chipta tekshiruvini butunlay o'chirardi.
    if security.internal_token_ok(token):
        return {"ok": True}

    # Tashqaridan faqat tomosha — va faqat chipta bilan.
    if action in ("read", "playback"):
        if security.stream_access_ok(ip, path, token):
            return {"ok": True}
    # Rad etish JURNALGA yoziladi. Tashqaridan bu "video uzildi" bo'lib
    # ko'rinadi va sababini taxmin qilib bo'lmaydi — MediaMTX aynan
    # nima so'raganini bilish shart. Toshqin bo'lmasin uchun bir xil
    # (yo'l, sabab) juftligi daqiqada bir marta yoziladi.
    _log_denial(ip, action, path, token)
    raise HTTPException(401, "Oqimga ruxsat yo'q")


@router.get("/hls")
def hls_auth(request: Request):
    """Nginx `auth_request` uchun: shu HLS so'roviga ruxsat bormi.

    `hlsCDNSecret` yoqilganda MediaMTX `Authorization: Bearer` bilan
    kelgan so'rovni shartsiz o'tkazadi va bizning auth ilgagimizni
    (POST /auth/stream) umuman chaqirmaydi. Sarlavhani nginx qo'yadi,
    demak tomoshabin chiptasini ham nginx tekshirishi shart — aks holda
    slug'ni bilgan har kim kamerani ko'ra olardi. Shu endpoint aynan
    o'sha tekshiruv: nginx har bir /media/hls/ so'rovi uchun bu yerga
    kiradi, 204 — ruxsat, 401 — rad.

    Nima uchun umuman Bearer rejimiga o'tildi: sessiyali rejimda manba
    qisqa uzilsa MediaMTX muxerni yo'q qiladi, sessiya o'ladi va mijoz
    DOIMIY 401 oladi (o'lchov: o'sha manzil 96 marta ketma-ket 401,
    o'z-o'zidan tiklanmaydi). Bearer rejimida sessiya yo'q, ya'ni bekor
    bo'ladigan holat ham yo'q.

    Chipta faqat BIRINCHI so'rovda (master pleylist) keladi — MediaMTX
    Bearer rejimida bola pleylisti va segment manzillariga hech qanday
    parametr qo'shmaydi. Shuning uchun keyingi so'rovlar `(ip, yo'l)`
    sessiyasi orqali o'tadi; `stream_access_ok` shuni allaqachon
    bajaradi.
    """
    uri = request.headers.get("x-original-uri", "")
    ip = (request.headers.get("x-viewer-ip")
          or (request.client.host if request.client else ""))
    yol, _, query = uri.partition("?")
    bolaklar = [b for b in yol.split("/") if b]
    # /media/hls/<slug>/<resurs> -> <slug>/<resurs> (MediaMTX ko'rgan yo'l)
    if len(bolaklar) >= 3 and bolaklar[0] == "media" and bolaklar[1] == "hls":
        path = "/".join(bolaklar[2:])
    else:
        path = "/".join(bolaklar)
    params = parse_qs(query)
    token = (params.get("token") or [""])[0]
    if "session" in params:
        _bearer_yoq_ogohlantir(path)

    if security.internal_token_ok(token):
        return Response(status_code=204)
    if security.stream_access_ok(ip, path, token):
        return Response(status_code=204)
    _log_denial(ip, "read", path, token)
    raise HTTPException(401, "Oqimga ruxsat yo'q")


_BEARER_WARN_EVERY = 300.0
_bearer_warned: list[float] = [0.0]


def _bearer_yoq_ogohlantir(path: str) -> None:
    """Manzilda `session=` bor — demak nginx Bearer sarlavhasini qo'ymayapti.

    Sessiyasiz (CDN) rejimda MediaMTX manzillarga hech qanday parametr
    qo'shmaydi. `session=` ko'rinishi bitta narsani anglatadi: shu so'rov
    kelgan nginx blokida

        proxy_set_header Authorization "Bearer <kalit>";

    yo'q yoki kaliti mos emas. Bu jimgina o'tib ketadigan nosozlik emas:
    sessiyali rejimda manba har uzilganda tomoshabin DOIMIY 401 oladi.
    Ishlab chiqarishda aynan shu bo'ldi — HTTPS (443) bloki eski namunadan
    ko'chirilgan edi, 80-portdagi blok esa to'g'ri. Shuning uchun muammo
    faqat https'da ko'rinardi.
    """
    now = time.monotonic()
    with _fails_lock:
        if now - _bearer_warned[0] < _BEARER_WARN_EVERY:
            return
        _bearer_warned[0] = now
    log("auth", "hls_bearer_yoq", level="warning", path=path,
        sabab="nginx /media/hls/ blokida Authorization: Bearer yo'q yoki "
              "kalit mos emas — MediaMTX sessiyali rejimda ishlayapti va "
              "manba uzilganda tomoshabin doimiy 401 oladi",
        yechim="python scripts/nginx_conf.py > /etc/nginx/sites-available/"
               "negoh.conf && nginx -t && systemctl reload nginx")


_DENY_EVERY = 60.0
_denied: dict[tuple, float] = {}


def _log_denial(ip: str, action: str, path: str, token: str) -> None:
    why = ("chiptasiz" if not token else
           "chipta muddati tugagan yoki imzo mos emas")
    key = (path, why)
    now = time.monotonic()
    with _fails_lock:
        if now - _denied.get(key, 0.0) < _DENY_EVERY:
            return
        _denied[key] = now
        if len(_denied) > 500:
            for k, t in list(_denied.items()):
                if now - t > _DENY_EVERY:
                    _denied.pop(k, None)
    log("auth", "stream_denied", level="warning",
        path=path, action=action, ip=ip, sabab=why,
        chipta=(token[:24] + "…") if token else "")


@ui_router.post("/login")
def login(body: LoginIn, request: Request, response: Response):
    ip = _client_ip(request)
    kutish = _retry_after(ip)
    if kutish:
        # Parol tekshirilmaydi — hisob ham oshmaydi, aks holda tinmay
        # urinayotgan hujumchi shu ip'ni cheksiz qulflab qo'yardi.
        soniya = max(1, math.ceil(kutish))
        log("auth", "login_throttled", level="warning", ip=ip, kutish=soniya)
        raise HTTPException(
            429, f"Juda ko'p urinish — {soniya} soniyadan keyin qayta urining",
            headers={"Retry-After": str(soniya)})

    with get_db() as db:
        security.purge_expired_sessions(db)
        row = db.execute(
            "SELECT id, username, pw_hash, pw_salt, role FROM admins "
            "WHERE username = ?",
            (body.username,),
        ).fetchone()
        if row is None or not security.verify_password(
            body.password, row["pw_hash"], row["pw_salt"]
        ):
            _note_fail(ip)
            log("auth", "login_failed", level="warning",
                ip=ip, username=body.username)
            raise HTTPException(401, "Login yoki parol noto'g'ri")
        _clear_fails(ip)
        token = security.create_session(db, row["id"])
        username, role = row["username"], row["role"]

    response.set_cookie(
        security.SESSION_COOKIE, token, httponly=True, samesite="lax",
        max_age=security.SESSION_HOURS * 3600, path="/",
        # HTTPS orqali kelgan bo'lsa cookie faqat HTTPS'da yuborilsin —
        # 80-portdagi blok (yoki http'ga tushib qolgan havola) sessiyani
        # ochiq tarmoqqa chiqarib yubormasin. Lokal http bilan ishlaganda
        # bayroq qo'yilmaydi, aks holda debug UI umuman kira olmasdi.
        secure=_https_dami(request),
    )
    return {"username": username, "role": role}


@ui_router.post("/logout")
def logout(request: Request, response: Response):
    with get_db() as db:
        security.delete_session(db, request.cookies.get(security.SESSION_COOKIE))
    response.delete_cookie(security.SESSION_COOKIE, path="/")
    return {"ok": True}


@ui_router.get("/me")
def me(request: Request):
    token = request.cookies.get(security.SESSION_COOKIE)
    with get_db() as db:
        user = security.session_admin(db, token)
    if user is None:
        return {"authenticated": False}
    return {"authenticated": True, "username": user["username"],
            "role": user["role"], "regions": []}

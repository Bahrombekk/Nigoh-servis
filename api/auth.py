"""Nigoh — autentifikatsiya endpointlari."""
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
# IP bo'yicha eksponensial kechikish: dastlabki 5 xato jazosiz (barmoq
# xatosi uchun), keyin har xato kutishni ikki baravar oshiradi
# (1s, 2s, 4s ... eng ko'pi 30s). So'rov javob olishdan oldin shu yerda
# kutib turadi — parol terish qurollari sekinlashadi. Har xato jurnalga
# `login_failed` bo'lib yoziladi — fail2ban shu satr bo'yicha ip'ni
# butunlay bloklashi mumkin.

_FAIL_FREE = 5           # shu songacha kechikish yo'q
_FAIL_MAX_DELAY = 30.0   # soniya
_FAIL_TTL = 3600.0       # soniya — shuncha tinch turgan ip hisobi unutiladi
_fails: dict[str, tuple[int, float]] = {}    # ip -> (xato soni, oxirgi vaqt)
_fails_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    # Nginx ortida haqiqiy manzil X-Forwarded-For'da (DEPLOY.md namunasi
    # uni har doim qo'yadi); to'g'ridan ulanishda socket manzili.
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else ""


def _fail_delay(ip: str) -> float:
    now = time.monotonic()
    with _fails_lock:
        if len(_fails) > 1000:               # xotira cheksiz o'smasin
            for k, (_, t) in list(_fails.items()):
                if now - t > _FAIL_TTL:
                    _fails.pop(k, None)
        count, last = _fails.get(ip, (0, 0.0))
        if now - last > _FAIL_TTL:
            count = 0
        if count < _FAIL_FREE:
            return 0.0
        return min(2.0 ** (count - _FAIL_FREE), _FAIL_MAX_DELAY)


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
    delay = _fail_delay(ip)
    if delay:
        time.sleep(delay)   # sync endpoint threadpool'da — boshqalarni bloklamaydi

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

"""Nigoh — autentifikatsiya endpointlari."""
from urllib.parse import parse_qs

from fastapi import APIRouter, HTTPException, Request, Response

from core import security
from core.db import get_db

from .models import LoginIn

# Prefiks nisbiy — create_app uni /api/v1 (asosiy) va /api (eski) ostida ulaydi.
router = APIRouter(prefix="/auth", tags=["auth"])


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
    raise HTTPException(401, "Oqimga ruxsat yo'q")


@router.post("/login")
def login(body: LoginIn, response: Response):
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
            raise HTTPException(401, "Login yoki parol noto'g'ri")
        token = security.create_session(db, row["id"])
        username, role = row["username"], row["role"]

    response.set_cookie(
        security.SESSION_COOKIE, token, httponly=True, samesite="lax",
        max_age=security.SESSION_HOURS * 3600, path="/",
    )
    return {"username": username, "role": role}


@router.post("/logout")
def logout(request: Request, response: Response):
    with get_db() as db:
        security.delete_session(db, request.cookies.get(security.SESSION_COOKIE))
    response.delete_cookie(security.SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(request: Request):
    token = request.cookies.get(security.SESSION_COOKIE)
    with get_db() as db:
        user = security.session_admin(db, token)
        regions = (security.user_regions(db, user["id"])
                   if user is not None and user["role"] == "operator" else [])
    if user is None:
        return {"authenticated": False}
    return {"authenticated": True, "username": user["username"],
            "role": user["role"], "regions": regions}

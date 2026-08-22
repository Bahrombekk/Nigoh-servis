"""Nigoh — super-admin autentifikatsiyasi va kamera parollarini shifrlash.

Kamera parollari MediaMTX uchun ochiq holda kerak bo'ladi, shuning uchun
ular qaytariladigan shifr (Fernet) bilan saqlanadi. Kalit `secret.key`
faylida turadi — bu fayl bazaning o'zi kabi maxfiy.

Admin paroli esa qaytarilmaydigan hash (scrypt) sifatida saqlanadi.
"""
import base64
import hashlib
import hmac
import os
import secrets
import threading
import time
from datetime import datetime, timedelta, timezone

from cryptography.fernet import Fernet, InvalidToken

from .db import DATA_DIR

# Kalit fayli ma'lumotlar katalogida — baza bilan yonma-yon turadi.
KEY_PATH = DATA_DIR / "secret.key"

SESSION_COOKIE = "nigoh_session"
SESSION_HOURS = 12


# ---------- kamera parollarini shifrlash ----------

def _load_key() -> bytes:
    if KEY_PATH.exists():
        return KEY_PATH.read_bytes().strip()
    key = Fernet.generate_key()
    KEY_PATH.write_bytes(key)
    try:  # faqat egasi o'qiy olsin (Windows'da e'tiborsiz qoldiriladi)
        os.chmod(KEY_PATH, 0o600)
    except OSError:
        pass
    return key


_fernet = Fernet(_load_key())


def encrypt(plain: str) -> str:
    return _fernet.encrypt(plain.encode()).decode()


def decrypt(token: str | None) -> str:
    if not token:
        return ""
    try:
        return _fernet.decrypt(token.encode()).decode()
    except InvalidToken:
        # Kalit almashtirilgan yoki yozuv buzilgan.
        return ""


# ---------- oqim chiptalari (MediaMTX kirish nazorati) ----------
#
# MediaMTX portlari (8554/8888/8889) hammaga ochiq edi: slug'ni bilgan har
# kim saytga kirmasdan kamerani ko'ra olardi. Endi MediaMTX har bir o'qish
# so'rovini backend'dan so'raydi (authMethod: http), backend esa saytdan
# berilgan qisqa muddatli chiptani tekshiradi.
#
# HLS'ning nozik joyi: brauzer tokenni faqat birinchi so'rovga (index.m3u8)
# qo'shadi, segment so'rovlariga emas. Shuning uchun to'g'ri token kelganda
# (ip, yo'l) juftligiga qisqa "sessiya" ochiladi — segmentlar shu sessiya
# orqali yuradi va har ruxsatli so'rovda uzayadi.

STREAM_TOKEN_TTL = 3600        # soniya — ulanishni boshlash uchun
STREAM_SESSION_TTL = 600       # soniya — har ruxsatli so'rovda uzayadi

# Kalit secret.key'dan hosil qilinadi — alohida fayl kerak emas.
_stream_key = hashlib.sha256(b"nigoh-stream:" + _load_key()).digest()
_stream_sessions: dict[tuple[str, str], float] = {}
_stream_lock = threading.Lock()


def _stream_sig(path: str, expires: int) -> str:
    mac = hmac.new(_stream_key, f"{path}|{expires}".encode(), hashlib.sha256)
    return base64.urlsafe_b64encode(mac.digest()[:20]).decode().rstrip("=")


def _sig_matches(sig: str, path: str, expires: int) -> bool:
    """Chipta shu yo'lga (yoki uning ichki resursiga) tegishlimi.

    Chipta oqim yo'liga imzolanadi ("kamera_1"), lekin MediaMTX ba'zi
    so'rovlarda ICHKI resurs bilan murojaat qiladi — masalan HLS variant
    playlisti "kamera_1/video1_stream.m3u8". Aynan tekshirilsa bunday
    so'rov rad etilardi va tomosha o'rtasida video uzilardi (o'lchov:
    tomosha boshlangandan ~25-45 soniya keyin 401 boshlanardi).

    Prefiks bo'yicha moslik xavfsiz: "kamera_1" chiptasi faqat
    "kamera_1" va uning ichidagi resurslarga ruxsat beradi. Yondosh
    "kamera_10" ga o'tmaydi — chegara sifatida "/" talab qilinadi.
    """
    if hmac.compare_digest(sig, _stream_sig(path, expires)):
        return True
    base = path.split("/", 1)[0]
    if base and base != path:
        return hmac.compare_digest(sig, _stream_sig(base, expires))
    return False


def stream_token(path: str) -> str:
    """Bitta yo'l uchun imzolangan chipta — oqim manziliga ?token= bo'lib qo'shiladi."""
    expires = int(time.time()) + STREAM_TOKEN_TTL
    return f"{expires}.{_stream_sig(path, expires)}"


def stream_access_ok(ip: str, path: str, token: str) -> bool:
    """MediaMTX'dan kelgan o'qish so'rovini tekshiradi.

    To'g'ri token — ruxsat + (ip, yo'l) sessiyasi. Tokensiz so'rov faqat
    tirik sessiya bo'lsa o'tadi (HLS segmentlari, WHEP davomi).
    """
    now = time.time()
    key = (ip, path)
    if token:
        expires_s, _, sig = token.partition(".")
        try:
            expires = int(expires_s)
        except ValueError:
            expires = 0
        if expires > now and _sig_matches(sig, path, expires):
            with _stream_lock:
                if len(_stream_sessions) > 10_000:      # chegara: eskilar chiqsin
                    for k in [k for k, t in _stream_sessions.items() if t <= now]:
                        _stream_sessions.pop(k, None)
                _stream_sessions[key] = now + STREAM_SESSION_TTL
            return True
    with _stream_lock:
        alive = _stream_sessions.get(key, 0.0) > now
        if alive:
            _stream_sessions[key] = now + STREAM_SESSION_TTL
    return alive


# ---------- ichki jarayonlar chiptasi ----------
#
# Launcher'ning FFmpeg'i (o'girish) va snapshot zaxirasi MediaMTX'ga
# 127.0.0.1 dan ulanadi, lekin IP'ga ishonib bo'lmaydi: nginx proksi
# ortida barcha tomoshabin ham 127.0.0.1 bo'lib ko'rinadi. Shuning uchun
# ichki jarayonlar muddatsiz, secret.key'dan hosil qilingan alohida
# chipta bilan yuradi — kalit almashsa chipta ham almashadi.

_internal_key = hashlib.sha256(b"nigoh-internal:" + _load_key()).digest()


def internal_token() -> str:
    """Ichki jarayonlar (FFmpeg) uchun muddatsiz chipta."""
    return base64.urlsafe_b64encode(_internal_key[:20]).decode().rstrip("=")


def internal_token_ok(token: str) -> bool:
    return bool(token) and hmac.compare_digest(token, internal_token())


# ---------- admin paroli ----------

def hash_password(password: str, salt: str | None = None) -> tuple[str, str]:
    salt = salt or secrets.token_hex(16)
    digest = hashlib.scrypt(
        password.encode(), salt=bytes.fromhex(salt), n=2**14, r=8, p=1, dklen=32
    )
    return base64.b64encode(digest).decode(), salt


def verify_password(password: str, pw_hash: str, salt: str) -> bool:
    candidate, _ = hash_password(password, salt)
    return hmac.compare_digest(candidate, pw_hash)


# ---------- sessiyalar ----------

def create_session(db, admin_id: int) -> str:
    token = secrets.token_urlsafe(32)
    expires = datetime.now(timezone.utc) + timedelta(hours=SESSION_HOURS)
    db.execute(
        "INSERT INTO sessions (token, admin_id, expires_at) VALUES (?, ?, ?)",
        (token, admin_id, expires.isoformat()),
    )
    return token


def session_admin(db, token: str | None):
    """Yaroqli sessiya bo'lsa admin yozuvini, aks holda None qaytaradi."""
    if not token:
        return None
    row = db.execute(
        "SELECT s.expires_at, a.id, a.username, a.role "
        "FROM sessions s JOIN admins a ON a.id = s.admin_id "
        "WHERE s.token = ?",
        (token,),
    ).fetchone()
    if row is None:
        return None
    if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
        db.execute("DELETE FROM sessions WHERE token = ?", (token,))
        return None
    return row


def delete_session(db, token: str | None) -> None:
    if token:
        db.execute("DELETE FROM sessions WHERE token = ?", (token,))


def purge_expired_sessions(db) -> None:
    db.execute(
        "DELETE FROM sessions WHERE expires_at < ?",
        (datetime.now(timezone.utc).isoformat(),),
    )


# ---------- admin yaratish ----------

def ensure_admin(db) -> str | None:
    """Birinchi ishga tushishda super-admin yaratadi.

    Parol ADMIN_PAROL muhit o'zgaruvchisidan olinadi; berilmagan bo'lsa
    tasodifiy parol yaratiladi va konsolga chiqarish uchun qaytariladi.
    """
    if db.execute("SELECT COUNT(*) FROM admins").fetchone()[0] > 0:
        return None

    username = os.environ.get("ADMIN_LOGIN", "admin")
    password = os.environ.get("ADMIN_PAROL")
    generated = password is None
    if generated:
        password = secrets.token_urlsafe(9)

    pw_hash, salt = hash_password(password)
    db.execute(
        "INSERT INTO admins (username, pw_hash, pw_salt) VALUES (?, ?, ?)",
        (username, pw_hash, salt),
    )
    return password if generated else None


def set_password(db, username: str, password: str) -> bool:
    pw_hash, salt = hash_password(password)
    cur = db.execute(
        "UPDATE admins SET pw_hash = ?, pw_salt = ? WHERE username = ?",
        (pw_hash, salt, username),
    )
    if cur.rowcount == 0:
        db.execute(
            "INSERT INTO admins (username, pw_hash, pw_salt) VALUES (?, ?, ?)",
            (username, pw_hash, salt),
        )
    # Parol almashgach eski sessiyalar bekor qilinadi.
    db.execute(
        "DELETE FROM sessions WHERE admin_id IN (SELECT id FROM admins WHERE username = ?)",
        (username,),
    )
    return True

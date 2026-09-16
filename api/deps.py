"""Nigoh — /v1 endpointlari uchun yagona kirish tekshiruvi.

Rollar va foydalanuvchi boshqaruvi asosiy tizimda — bu servis uchun
bitta savol bor: so'rov ishonchli manbadanmi? Javob `X-API-Key`.
Debug UI yoqilganda (ENABLE_UI=1) cookie sessiyasi ham o'tadi — panel
ishlashi uchun; ishlab chiqarishda UI o'chiriladi va faqat kalit qoladi.
"""
import math

from fastapi import HTTPException, Request

from core.log import log
from core.throttle import Throttle

from .config import ENABLE_UI
from .helpers import api_key_ok, client_ip, current_user

# Noto'g'ri kalit bilan kelgan urinishlar — ip bo'yicha sekinlashtiriladi.
#
# Kalit 64 belgili bo'lsa uni taxmin qilib bo'lmaydi, lekin cheklov
# baribir kerak: kalit KALTA yoki sizib chiqqan bo'lishi mumkin, va
# tekshiruvsiz endpoint cheksiz tezlikda urishga ochiq qoladi. Kirish
# formasida shunday himoya allaqachon bor edi — bu yerda yo'q edi.
#
# Bepul urinish kamroq (kalit odam yodlaydigan narsa emas: mijoz uni
# sozlamadan oladi, ya'ni xato urinish normal holat emas).
_key_throttle = Throttle(free=3, max_delay=30.0, ttl=600.0)


def require_key(request: Request) -> None:
    """Barcha /v1 yo'llari uchun dependency: kalitsiz so'rov — 401."""
    if api_key_ok(request):
        _key_throttle.clear(client_ip(request))
        return
    if ENABLE_UI and current_user(request) is not None:
        return

    # Cheklov faqat KALIT BERILGAN-u, xato bo'lgan holatga. Sarlavha
    # umuman yo'q bo'lsa — bu odatda noto'g'ri sozlangan mijoz yoki
    # oddiy probe, taxmin urinishi emas; uni cheklash foydasiz, lekin
    # halol integratsiyani shovqinli buzadi.
    if not request.headers.get("x-api-key"):
        raise HTTPException(401, "X-API-Key sarlavhasi kerak")

    ip = client_ip(request)
    kutish = _key_throttle.retry_after(ip)
    if kutish:
        # So'rov ICHIDA uxlanmaydi — javob darhol qaytadi, mijoz o'zi
        # kutadi. Uxlash jarayonning ishchi oqimlarini band qilardi va
        # shu bilan cheklovning o'zi DoS quroliga aylanardi.
        soniya = max(1, math.ceil(kutish))
        raise HTTPException(
            429, f"Juda ko'p urinish — {soniya} soniyadan keyin qayta urining",
            headers={"Retry-After": str(soniya)})

    _key_throttle.note_fail(ip)
    log("auth", "api_key_xato", level="warning", ip=ip,
        path=request.url.path)
    raise HTTPException(401, "X-API-Key sarlavhasi kerak")

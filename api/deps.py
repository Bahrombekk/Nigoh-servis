"""Nigoh — /v1 endpointlari uchun yagona kirish tekshiruvi.

Rollar va foydalanuvchi boshqaruvi asosiy tizimda — bu servis uchun
bitta savol bor: so'rov ishonchli manbadanmi? Javob `X-API-Key`.
Debug UI yoqilganda (ENABLE_UI=1) cookie sessiyasi ham o'tadi — panel
ishlashi uchun; ishlab chiqarishda UI o'chiriladi va faqat kalit qoladi.
"""
from fastapi import HTTPException, Request

from .config import ENABLE_UI
from .helpers import api_key_ok, current_user


def require_key(request: Request) -> None:
    """Barcha /v1 yo'llari uchun dependency: kalitsiz so'rov — 401."""
    if api_key_ok(request):
        return
    if ENABLE_UI and current_user(request) is not None:
        return
    raise HTTPException(401, "X-API-Key sarlavhasi kerak")

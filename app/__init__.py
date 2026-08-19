"""Nigoh — FastAPI ilovasini yig'ish.

Qatlamlar:

    app/config.py         sozlamalar (portlar, shablonlar)
    app/models.py         so'rov modellari (Pydantic)
    app/helpers.py        umumiy tarjima qatlami (baza → brauzer/MediaMTX)
    app/routes_auth.py    /api/v1/auth/*
    app/routes_public.py  /api/v1/cameras/*   (kirishsiz)
    app/routes_streams.py /api/v1/streams     (batch oqim chiptalari)
    app/routes_events.py  /api/v1/events      (SSE — holat o'zgarishlari)
    app/routes_devices.py /api/v1/devices/*   (skan: job + SSE natijalar)
    app/routes_admin.py   /api/v1/admin/*     (kameralar CRUD, tugunlar)

API ikki prefiksda tinglaydi:

    /api/v1/...   asosiy, hujjatlangan manzil — tashqi mijozlar shu bilan
                  ishlasin; hujjat: /docs (Swagger) va /redoc.
    /api/...      eski manzillar aynan shu endpointlarga olib boradi —
                  ichki test interfeys va MediaMTX auth (STREAM_AUTH_URL)
                  buzilmasin deb saqlab qolingan; hujjatda ko'rinmaydi.

MediaMTX bilan aloqa alohida `media/` paketida — backend unga faqat
`from media import sync` orqali murojaat qiladi. Umumiy infratuzilma
(db, security, health, rtsp_probe, fast_start) `core/` paketida.
"""
import asyncio

from fastapi import Depends, FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from core import bus
from core.db import BASE_DIR

from .config import VENDORS
from .deps import require_key
from .routes_admin import router as admin_router
from .routes_auth import router as auth_router
from .routes_devices import router as devices_router
from .routes_events import router as events_router
from .routes_public import router as public_router
from .routes_streams import router as streams_router

API_DESCRIPTION = """\
IP kameralarni boshqarish va tarqatish servisi. MediaMTX ustidagi
boshqaruv qatlami: kameralar bazada, oqim yo'llari MediaMTX Control API
orqali dinamik boshqariladi, restart hech qachon kerak emas.

Kirish: barcha endpointlar `X-API-Key` sarlavhasini talab qiladi
(`NIGOH_API_KEY`). Debug UI yoqilganda (ENABLE_UI=1) cookie sessiyasi
ham o'tadi. Istisno — **auth**: `/auth/stream` ni MediaMTX'ning o'zi
chaqiradi (chipta tekshiruvi), `/auth/login` — debug UI kirishi.

Bo'limlar:

* **cameras** — ro'yxat, batch holat, oqim manzili (chiptali), surat.
* **streams** — batch oqim chiptalari (bitta so'rovda 128 tagacha).
* **events** — SSE: holat o'zgarishlari jonli.
* **devices** — skan (SSE), qurilma pasporti.
* **admin** — kameralar CRUD, NVR import, MediaMTX tugunlari va
  sinxronlash. Xarita, rollar va dashboard asosiy tizimda.

Kamera holati (`state`): `disabled / unknown / offline / stalled / online`.
Tugun holati (`status`): `online / degraded / offline`.
"""


def create_app() -> FastAPI:
    app = FastAPI(
        title="Nigoh API",
        version="1.0",
        description=API_DESCRIPTION,
    )

    @app.on_event("startup")
    async def _bus_loop():
        # Fon thread'lari (health, reconciler) SSE hodisalarini shu loop
        # orqali yetkazadi.
        bus.set_loop(asyncio.get_running_loop())

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(BASE_DIR / "static" / "index.html")

    @app.get("/api/v1/vendors", tags=["cameras"],
             dependencies=[Depends(require_key)])
    @app.get("/api/vendors", include_in_schema=False,
             dependencies=[Depends(require_key)])
    def list_vendors():
        """Kamera qo'shishda tanlanadigan tayyor RTSP shablonlari."""
        return VENDORS

    # /api/v1 — asosiy (hujjatlangan); /api — eski manzillar, xuddi shu
    # endpointlar (test frontend va MediaMTX auth manzili buzilmasin).
    #
    # Yagona kirish — X-API-Key (deps.require_key): auth routeridan
    # boshqa hamma narsa kalit talab qiladi. auth alohida: /auth/stream ni
    # MediaMTX chaqiradi (o'z chipta tekshiruvi bor), /auth/login — debug
    # UI kirishi.
    app.include_router(auth_router, prefix="/api/v1")
    app.include_router(auth_router, prefix="/api", include_in_schema=False)
    for router in (public_router, streams_router, events_router,
                   devices_router, admin_router):
        app.include_router(router, prefix="/api/v1",
                           dependencies=[Depends(require_key)])
        app.include_router(router, prefix="/api", include_in_schema=False,
                           dependencies=[Depends(require_key)])

    # Qolgan static fayllar (style.css, app.js) — yuqoridagi maxsus
    # yo'llardan keyin ulanadi, shuning uchun ular ustun turadi.
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    return app

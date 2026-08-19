"""Nigoh — FastAPI ilovasini yig'ish.

Qatlamlar:

    app/config.py         sozlamalar (portlar, shablonlar)
    app/models.py         so'rov modellari (Pydantic)
    app/helpers.py        umumiy tarjima qatlami (baza → brauzer/MediaMTX)
    app/routes_auth.py    /api/v1/auth/*
    app/routes_public.py  /api/v1/cameras/*   (kirishsiz)
    app/routes_streams.py /api/v1/streams     (batch oqim chiptalari)
    app/routes_stats.py   /api/v1/stats/*     (kirishsiz — dashboard tarixi)
    app/routes_admin.py   /api/v1/admin/*     (super-admin)

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
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from core.db import BASE_DIR

from .config import VENDORS
from .routes_admin import router as admin_router
from .routes_auth import router as auth_router
from .routes_public import router as public_router
from .routes_stats import router as stats_router
from .routes_streams import router as streams_router

API_DESCRIPTION = """\
IP kameralarni boshqarish va tarqatish servisi. MediaMTX ustidagi
boshqaruv qatlami: kameralar bazada, oqim yo'llari MediaMTX Control API
orqali dinamik boshqariladi, restart hech qachon kerak emas.

Bo'limlar:

* **cameras** — ochiq: xarita ro'yxati, oqim manzili (chiptali), surat.
* **stats** — ochiq: dashboard tarixi (24 soat / 7 kun).
* **auth** — kirish/chiqish; `POST /auth/stream` ni MediaMTX'ning o'zi
  chaqiradi (oqimga ruxsat tekshiruvi), brauzer emas.
* **admin** — faqat `admin` roli: kameralar CRUD, NVR import, skaner,
  foydalanuvchilar, MediaMTX tugunlari va sinxronlash.

Rollar: `admin` hammasini ko'radi va boshqaradi; `operator` faqat o'ziga
biriktirilgan hududlardagi kameralarni ko'radi. Anonim ko'rish standart
holda ochiq (`PUBLIC_VIEW=0` — faqat tizimga kirganlar ko'radi).

Kamera holati (`state`): `disabled / unknown / offline / stalled / online`.
Tugun holati (`status`): `online / degraded / offline`.
"""


def create_app() -> FastAPI:
    app = FastAPI(
        title="Nigoh API",
        version="1.0",
        description=API_DESCRIPTION,
    )

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(BASE_DIR / "static" / "index.html")

    @app.get("/static/uz.geojson", include_in_schema=False)
    def uz_boundary():
        """O'zbekiston chegarasi (OSM, ADM0) — xaritada hududni ajratish uchun."""
        return FileResponse(BASE_DIR / "static" / "uz.geojson",
                            media_type="application/geo+json",
                            headers={"Cache-Control": "max-age=86400"})

    @app.get("/static/uz_regions.geojson", include_in_schema=False)
    def uz_regions():
        """Viloyat chegaralari (ADM1) — nuqtadan hududni aniqlash uchun."""
        return FileResponse(BASE_DIR / "static" / "uz_regions.geojson",
                            media_type="application/geo+json",
                            headers={"Cache-Control": "max-age=86400"})

    @app.get("/api/v1/vendors", tags=["cameras"])
    @app.get("/api/vendors", include_in_schema=False)
    def list_vendors():
        """Kamera qo'shishda tanlanadigan tayyor RTSP shablonlari."""
        return VENDORS

    # /api/v1 — asosiy (hujjatlangan); /api — eski manzillar, xuddi shu
    # endpointlar (test frontend va MediaMTX auth manzili buzilmasin).
    for router in (auth_router, public_router, streams_router, stats_router,
                   admin_router):
        app.include_router(router, prefix="/api/v1")
        app.include_router(router, prefix="/api", include_in_schema=False)

    # Qolgan static fayllar (style.css, app.js) — yuqoridagi maxsus
    # yo'llardan keyin ulanadi, shuning uchun ular ustun turadi.
    app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

    return app

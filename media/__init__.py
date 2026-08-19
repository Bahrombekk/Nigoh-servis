"""Nigoh — MediaMTX bilan bog'liq hamma narsa shu paketda.

    media/sync.py       konfiguratsiya, jonli API, FFmpeg buyruqlari
    media/launcher.py   talab bo'yicha o'girish jarayoni
    stream_launcher.py  (ildizda) MediaMTX chaqiradigan yupqa qobiq

Backend bilan chegara: `from media import sync` — boshqa yo'l yo'q.
MediaMTX'ning o'zi (mediamtx/ papkasi) va mediamtx.yml ildizda qoladi —
ularni ishga-tushirish.bat boshqaradi.
"""
from . import sync  # noqa: F401

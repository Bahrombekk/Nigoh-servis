"""Nigoh — sozlamalar.

Hammasi muhit o'zgaruvchilari orqali boshqariladi, standart qiymatlar
bitta kompyuterda ishga tushirishga mo'ljallangan.
"""
import os

PORT = int(os.environ.get("PORT", "8010"))

# Debug UI (xarita, admin panel): 1 — cookie login ishlaydi va sahifa
# beriladi; 0 — faqat API. Bu servisning haqiqat manbai X-API-Key, UI
# faqat "backend'da xatomi yoki kamerada?" savoliga javob vositasi.
ENABLE_UI = os.environ.get("ENABLE_UI", "1") != "0"

# Yagona kirish: tashqi backend `X-API-Key` sarlavhasi bilan kiradi —
# cookie/login kerak emas. Ishga tushirish uchun MAJBURIY (bo'sh bo'lsa
# servis ko'tarilmaydi). Uzun tasodifiy qiymat qo'ying (masalan,
# `openssl rand -hex 32`).
API_KEY = os.environ.get("NIGOH_API_KEY", "")
HLS_PORT = int(os.environ.get("HLS_PORT", "8888"))
WEBRTC_PORT = int(os.environ.get("WEBRTC_PORT", "8889"))
MEDIA_HOST = os.environ.get("MEDIA_HOST", "")  # bo'sh bo'lsa so'rov manzilidan olinadi

# Sayt HTTPS proksi (nginx) ortida bo'lsa, oqim manzillari ham HTTPS bo'lishi
# shart — aks holda brauzer videoni bloklaydi (mixed content). MEDIA_BASE
# to'liq asos beradi (masalan, https://kamera.example.uz/media) va proksi
# /hls/ ni 8888-ga, /whep/ ni 8889-ga o'tkazadi. Bo'sh qolsa eski usul:
# http://MEDIA_HOST:port. Faqat markaziy (1-) tugunga taalluqli.
MEDIA_BASE = os.environ.get("MEDIA_BASE", "").rstrip("/")

# Kamerani qo'shishda tanlanadigan tayyor RTSP shablonlari.
VENDORS = [
    {"id": "hikvision", "name": "Hikvision", "path": "/Streaming/Channels/101", "port": 554},
    {"id": "dahua", "name": "Dahua", "path": "/cam/realmonitor?channel=1&subtype=0", "port": 554},
    {"id": "uniview", "name": "Uniview", "path": "/media/video1", "port": 554},
    {"id": "axis", "name": "Axis", "path": "/axis-media/media.amp", "port": 554},
    {"id": "tplink", "name": "TP-Link / Tapo", "path": "/stream1", "port": 554},
    {"id": "reolink", "name": "Reolink", "path": "/h264Preview_01_main", "port": 554},
    {"id": "amcrest", "name": "Amcrest", "path": "/cam/realmonitor?channel=1&subtype=0", "port": 554},
    {"id": "holowits", "name": "Holowits / Huawei", "path": "/LiveMedia/ch1/Media1", "port": 554},
    {"id": "boshqa", "name": "Boshqa (qo'lda)", "path": "/stream1", "port": 554},
]

# Kanal raqami bilan ishlaydigan (NVR bo'la oladigan) ishlab chiqaruvchilar —
# skaner shu tartibda sinaydi, birinchi javob bergani tanlanadi.
CHANNEL_VENDORS = ["hikvision", "dahua", "holowits", "uniview", "reolink", "axis"]

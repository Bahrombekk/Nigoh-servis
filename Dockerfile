# Nigoh — kamera mikroservisi (control plane + MediaMTX bitta konteynerda).
#
# Nega bitta konteyner: MediaMTX'ning o'girish yo'llari (H.265 -> H.264)
# stream_launcher.py ni MediaMTX turgan mashinada chaqiradi, backend esa
# MediaMTX'ni o'zi kuzatib qayta ko'taradi (media/reconciler.py). Ikkalasini
# ajratish shu ikkala mexanizmni buzadi. Tashqaridan bu baribir bitta
# servis: HTTP API (8010) + media portlari.
#
# Qo'shimcha MediaMTX tugunlari (boshqa serverlarda) alohida, toza MediaMTX
# bo'lib turadi — ularga /api/v1/admin/nodes/{id}/config dan konfiguratsiya
# olinadi, bu image kerak emas.
FROM python:3.12-slim

# FFmpeg — H.265 kameralarni brauzer o'qiydigan H.264 ga o'girish uchun.
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*

# MediaMTX'ning o'zi — reconciler uni /app/mediamtx/mediamtx dan topadi.
#
# v1.20.0 da HLS uchun cookie/session-id mexanizmi bola (variant)
# pleylistiga tasodifiy "authentication error" (401) qaytaradi — mijoz
# bir necha soniya normal ko'radi, keyin 401 boshlanadi va master qayta
# olinmaguncha tiklanmaydi. Ma'lum xato: bluenviron/mediamtx#5700 va
# #5736. v1.20.1 da HLS sessiyasi plain HTTP'da cookie o'rniga so'rov
# parametriga o'tkazilgan ("stop using cookies with plain HTTP") va
# muammo yo'qoladi.
#
# O'lchov (bir xil kod, bir xil kamera, cookie bilan curl, to'g'ridan
# MediaMTX'ga 45 so'rov):
#     v1.20.0 (server) — 401: 4 / 19 / 0  (yugurishga qarab tasodifiy)
#     v1.20.1 (lokal)  — 401: 0
ARG MEDIAMTX_VERSION=v1.20.1
ARG MEDIAMTX_ARCH=amd64
RUN mkdir -p /app/mediamtx \
    && curl -fsSL "https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_linux_${MEDIAMTX_ARCH}.tar.gz" \
       | tar -xz -C /app/mediamtx mediamtx

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Barcha o'zgaruvchan ma'lumot (baza, kalit, loglar, mediamtx.yml) /data da —
# konteyner yangilansa ham kameralar va parollar joyida qoladi.
ENV NIGOH_DATA=/data
VOLUME /data

# Root emas: jarayon buzilsa ham konteyner ichida imtiyoz yo'q.
# UID 1000 — host'dagi birinchi foydalanuvchi bilan mos: ./data volume'ini
# `chown -R 1000:1000 data` qilib berish kifoya (DEPLOY.md).
RUN useradd --uid 1000 --user-group --no-create-home nigoh \
    && mkdir -p /data && chown nigoh:nigoh /data
USER nigoh

# 8010 API+UI · 8554 RTSP · 8888 HLS · 8889 WebRTC · 8189/udp WebRTC media
# 9997 MediaMTX API va 9998 metrics faqat 127.0.0.1 da — tashqariga ochilmaydi.
EXPOSE 8010 8554 8888 8889
EXPOSE 8189/udp

# Salomatlik: /health kalitsiz javob beradi; MediaMTX yiqilsa ok=false
# bo'lsa ham 200 qaytadi — konteyner tirikligi bilan servis salomatligi
# ajratilgan (reconciler MediaMTX'ni o'zi ko'taradi).
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s \
    CMD curl -fsS "http://127.0.0.1:${PORT:-8010}/health" || exit 1

CMD ["python", "main.py"]

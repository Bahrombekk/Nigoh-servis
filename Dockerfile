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
ARG MEDIAMTX_VERSION=v1.20.0
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

# 8010 API+UI · 8554 RTSP · 8888 HLS · 8889 WebRTC · 8189/udp WebRTC media
# 9997 MediaMTX API va 9998 metrics faqat 127.0.0.1 da — tashqariga ochilmaydi.
EXPOSE 8010 8554 8888 8889
EXPOSE 8189/udp

CMD ["python", "main.py"]

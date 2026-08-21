# Nigoh — serverga qo'yish

## Talablar

- Linux server (Ubuntu 22.04+ tavsiya), Docker va docker compose plugin.
- Server kameralar tarmog'iga yeta olishi kerak (RTSP, odatda 554-port).
- Video o'girish ko'p bo'lsa NVIDIA GPU foyda beradi, lekin shart emas —
  H.264 kameralar umuman o'girilmaydi.

## Tez boshlash

```bash
# 1. Loyihani serverga ko'chiring (git yoki papkani nusxalash)
cd nigoh

# 2. Sozlamalar — NIGOH_API_KEY majburiy (usiz servis ko'tarilmaydi)
cp .env.example .env
nano .env          # NIGOH_API_KEY (openssl rand -hex 32) va ADMIN_PAROL

# 3. Ma'lumot papkasi — konteyner root emas (uid 1000)
mkdir -p data && sudo chown -R 1000:1000 data

# 4. Ishga tushirish
docker compose up -d --build

# 5. Tekshirish
docker logs nigoh          # "Uvicorn running" ko'rinishi kerak
curl http://localhost:8010/health
curl -H "X-API-Key: SIZNING_KALIT" http://localhost:8010/api/v1/cameras
```

Barcha API `X-API-Key` bilan ishlaydi. Diagnostika konsoli standart holda o'chiq —
diagnostika kerak bo'lsa `.env` ga `ENABLE_UI=1` qo'ying, shunda
`http://SERVER_IP:8010` sahifasi ochiladi (login: `.env` dagi
`ADMIN_PAROL`; berilmagan bo'lsa parol `docker logs nigoh` chiqishida
bir marta ko'rinadi — saqlab qo'ying).

## Portlar

Konteyner host tarmog'ida ishlaydi (WebRTC/UDP uchun shart). Firewall'da
oching:

| Port | Protokol | Kimga | Nima |
|---|---|---|---|
| 8010 | tcp | foydalanuvchilar | API (va ENABLE_UI=1 bo'lsa konsol) |
| 8888 | tcp | foydalanuvchilar | HLS video |
| 8889 | tcp | foydalanuvchilar | WebRTC signal (WHEP) |
| 8189 | **udp va tcp** | foydalanuvchilar | WebRTC media (ICE) |
| 8554 | tcp | ixtiyoriy | RTSP chiqish (VLC va h.k.) — kerak bo'lmasa yopiq tuting |
| 9997, 9998 | tcp | hech kim | MediaMTX API/metrics — faqat 127.0.0.1, ochilmaydi |

**8189 ikkala protokolda ochilsin.** WebRTC avval UDP'ni sinaydi (eng
samarali), lekin korporativ firewall va ba'zi mobil operatorlarda UDP
yopiq bo'ladi — o'shanda ICE TCP zaxirasi ishga tushadi. TCP yopiq
qolsa brauzer jimgina HLS'ga tushadi, ya'ni eng sekin yo'lga:

```bash
sudo ufw allow 8189/udp
sudo ufw allow 8189/tcp
```

TCP zaxirasi kerak bo'lmasa `.env` da `WEBRTC_TCP_PORT=0` bilan
o'chiriladi.

Server NAT yoki domen ortida bo'lsa `.env` da `MEDIA_HOST` ga tashqi
IP/domenni yozing — oqim manzillari shu manzil bilan beriladi va o'sha
manzil brauzerga WebRTC uchun ham yuboriladi (`webrtcAdditionalHosts`).
Bir nechta manzil kerak bo'lsa — `WEBRTC_HOSTS=10.0.0.5,kamera.example.uz`.

## Ma'lumotlar va zaxira

Hamma o'zgaruvchan narsa `./data` papkasida (baza, `secret.key`, loglar).
Zaxira uchun shu papkani arxivlash yetarli:

```bash
tar czf nigoh-backup-$(date +%F).tar.gz data/
```

`secret.key` yo'qolsa kameralarning saqlangan parollari **tiklanmaydi** —
zaxirani alohida xavfsiz joyda ham saqlang.

Yangilash (ma'lumotlar joyida qoladi):

```bash
docker compose up -d --build
```

## HTTPS (tavsiya qilinadi)

Parollar ochiq HTTP orqali yurmasligi uchun oldiga nginx qo'ying va
`.env` da `MEDIA_BASE=https://kamera.example.uz/media` qo'ying — shunda
video ham shu domen ostidan (HTTPS) yuradi, brauzerning mixed-content
blokiga tushmaydi va 8888/8889-portlarni tashqariga ochish shart emas.

`X-Forwarded-For` majburiy: MediaMTX tomoshabinning haqiqiy IP'sini shu
sarlavhadan oladi (aks holda hamma 127.0.0.1 bo'lib ko'rinadi va HLS
sessiyalari aralashadi).

```nginx
server {
    listen 443 ssl;
    server_name kamera.example.uz;
    ssl_certificate     /etc/letsencrypt/live/kamera.example.uz/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/kamera.example.uz/privkey.pem;

    # API + UI
    location / {
        proxy_pass http://127.0.0.1:8010;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto https;
        proxy_set_header X-Forwarded-For $remote_addr;
    }

    # HLS video (MEDIA_BASE=/media bo'lganda)
    location /media/hls/ {
        proxy_pass http://127.0.0.1:8888/;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_buffering off;
    }

    # WebRTC signal (WHEP)
    location /media/whep/ {
        proxy_pass http://127.0.0.1:8889/;
        proxy_set_header X-Forwarded-For $remote_addr;
    }
}
```

WebRTC media (8189) proksisiz to'g'ridan ishlayveradi — firewall'da
**udp va tcp** ochiq tursin; ikkalasi ham ulanolmasa brauzer o'zi
HLS'ga o'tadi (sekinroq, lekin ishlaydi). Uzoq
MediaMTX tugunlari `MEDIA_BASE` ga kirmaydi — ular o'z `public_host`
manzilida qoladi.

## Bir nechta MediaMTX tuguni (kameralar har xil joylarda bo'lsa)

Kameralar bir necha binoda/shaharda bo'lsa, har joyga bitta MediaMTX
qo'ying — kamera trafigi lokal tarmoqda qoladi:

```bash
# Tugun serverida faqat toza MediaMTX kerak:
docker run -d --name mediamtx --network host \
  -v $PWD/mediamtx.yml:/mediamtx.yml bluenviron/mediamtx:latest
```

Konfiguratsiyani markaz beradi: admin sifatida
`GET /api/v1/admin/nodes/{id}/config` — tayyor `mediamtx.yml` (parolsiz).
Tugunni `POST /api/v1/admin/nodes` bilan ro'yxatga oling, kameralarni
`node_id` bilan biriktiring. Markaz tugun yo'llarini API orqali o'zi
boshqaradi (9997-portni faqat markaz serveriga oching), salomatligi
`GET /api/v1/admin/nodes` da ko'rinadi.

Eslatma: o'girish (H.265→H.264) faqat markaziy tugunda ishlaydi — uzoq
tugun kameralarini H.264 rejimida tuting.

## Docker'siz (muqobil)

```bash
apt install python3.12-venv ffmpeg
python3 -m venv venv && venv/bin/pip install -r requirements.txt
# MediaMTX binarini mediamtx/ papkasiga yuklab qo'ying (linux_amd64)
# NIGOH_API_KEY majburiy: `.env` ni bu yo'lda hech kim o'qimaydi.
export NIGOH_API_KEY=$(openssl rand -hex 32)
export NIGOH_DATA=/var/lib/nigoh PORT=8010
venv/bin/python main.py
```

systemd unit namunasi:

```ini
[Unit]
Description=Nigoh kamera servisi
After=network-online.target

[Service]
WorkingDirectory=/opt/nigoh
Environment=NIGOH_DATA=/var/lib/nigoh
# `.env` ni systemd ham, Python ham o'zi o'qimaydi — kalitni shu yerda
# bering. EnvironmentFile ishlatsangiz fayl huquqi 600 bo'lsin.
EnvironmentFile=/etc/nigoh.env
ExecStart=/opt/nigoh/venv/bin/python main.py
Restart=always
User=nigoh

[Install]
WantedBy=multi-user.target
```

## Muammolarni aniqlash

| Belgi | Qarash joyi |
|---|---|
| Sayt ochilmayapti | `docker logs nigoh` |
| Video ochilmayapti | `GET /api/v1/admin/status` — `mediamtx: true` bo'lishi kerak; `data/mediamtx.log` |
| Kamera qizil (offline) | serverdan kameraga tarmoq bormi: `POST /api/v1/admin/probe` sabab-bosqichini aytadi (tarmoq/parol/yo'l) |
| Oqim "stalled" | kamera portga javob beradi, lekin tasvir bermayapti — registratorda kanalni tekshiring |
| Hodisalar tarixi | `GET /api/v1/admin/events`, JSON log: `data/nigoh.log` |
| Kamera sekin ochilmoqda | `GET /health` → `open_ms`: `stream_ms` katta bo'lsa MediaMTX/tugun, `frame_ms` katta bo'lsa registratordagi `I Frame Interval` |
| Hamma kamera sekin ochilyapti | `GET /api/v1/admin/status` → `nodes[].pending_paths` noldan katta bo'lsa MediaMTX'da eski yo'llar qolgan: uni bir marta qayta ishga tushiring |
| WebRTC o'rniga doim HLS | 8189 **tcp** ham ochiqmi (ICE zaxirasi); `MEDIA_HOST`/`WEBRTC_HOSTS` to'g'rimi |

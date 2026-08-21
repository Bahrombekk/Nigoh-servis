# Nigoh — kamera servisi

IP kameralarni boshqarish va tarqatish mikroservisi. Asosiy tizimingiz
kamera **ID** sini beradi, Nigoh **oqim manzili, holat va surat**ni
qaytaradi. RTSP, kodeklar, MediaMTX, oqim chiptalari va kamera
salomatligi shu servis ichida qoladi.

Xarita, rollar, foydalanuvchi boshqaruvi va dashboard **asosiy
tizimda** — Nigoh ularni yuritmaydi.

| Kim uchun | Fayl |
|---|---|
| **Asosiy tizimga ulovchi** | [docs/INTEGRATION.md](docs/INTEGRATION.md) — to'liq API qo'llanma |
| Serverga qo'yuvchi | [docs/DEPLOY.md](docs/DEPLOY.md) — Docker, portlar, HTTPS, zaxira |
| Backendchi | [docs/BACKEND.md](docs/BACKEND.md) — kontrakt, auth, ichki tuzilish |
| Frontendchi | [docs/FRONTEND.md](docs/FRONTEND.md) — pleyer kodi, oqim ochish |
| Loyihani tushunmoqchi | [docs/TUSHUNTIRISH.md](docs/TUSHUNTIRISH.md) — qarorlar va sabablari |
| O'lchovlar | [docs/BENCHMARK.md](docs/BENCHMARK.md) — 5000 kamerada raqamlar |

## Ishga tushirish

Serverda (Docker):

```bash
cp .env.example .env
nano .env                  # NIGOH_API_KEY majburiy — usiz servis ko'tarilmaydi
mkdir -p data && sudo chown -R 1000:1000 data
docker compose up -d --build
```

Windows'da (lokal ishlab chiqish): **`ishga-tushirish.bat`** faylini ikki
marta bosing. U MediaMTX va servisni birga ishga tushiradi, brauzerni
ochadi va vaqtinchalik kalit yaratadi.

Qo'lda ishga tushirsangiz, **`.env` ni Python o'qimaydi** (`python-dotenv`
yo'q — uni faqat `docker compose` yuklaydi). Muhit o'zgaruvchilarini
o'zingiz bering:

```powershell
$env:NIGOH_API_KEY = "uzun-tasodifiy-kalit"   # openssl rand -hex 32
$env:ENABLE_UI = "1"                          # diagnostika konsoli kerak bo'lsa
venv\Scripts\python.exe main.py
```

Brauzerda: **http://localhost:8010** (port band bo'lsa `$env:PORT = "8020"`).

## API — asosiy kirish nuqtasi

Barcha endpointlar `X-API-Key` sarlavhasini talab qiladi. Interaktiv
hujjat serverning o'zida: **`/docs`** (Swagger) va **`/redoc`**.

| Bo'lim | Prefiks | Nima beradi |
|---|---|---|
| Kameralar | `/api/v1/cameras` | ro'yxat, holat, oqim manzili, surat |
| Batch chiptalar | `/api/v1/streams` | bitta so'rovda 128 tagacha oqim |
| Jonli holat | `/api/v1/events` | SSE — holat o'zgarishlari |
| Qurilma skani | `/api/v1/devices` | avtomatik aniqlash (SSE), pasport |
| Boshqaruv | `/api/v1/admin` | CRUD, NVR import, tugunlar |
| **Uzilishlar tahlili** | `/api/v1/admin/uptime`, `/admin/outages/hourly`, `/admin/cameras/{ref}/history` | uptime, guruh reytingi, soatlik profil |
| Ochilish o'lchovi | `/api/v1/metrics/open` | pleyer o'lchagan vaqt → `/health` da p50/p95 |
| Salomatlik | `/health` | ochiq (Docker HEALTHCHECK) |

Eski `/api/...` manzillari ham xuddi shu endpointlarga olib boradi
(ichki interfeys va MediaMTX auth buzilmasin deb), lekin hujjatda
faqat `/api/v1`.

Har bir kamerada yagona **`state`** maydoni bor: `disabled` · `unknown` ·
`offline` · `stalled` · `online`.

### Kirish va rollar

Rollar va hududlar asosiy tizimda. `NIGOH_API_KEY` majburiy — busiz
servis ishga tushmaydi. `/api/v1/admin/users` faqat diagnostika
konsoliga kiradigan qo'shimcha adminlar uchun.

## Diagnostika konsoli

Standart holda **o'chiq** (`ENABLE_UI=0`) — ishlab chiqarishda servis
faqat API. `ENABLE_UI=1` qilinsa `/` sahifasi va cookie login ochiladi.
U bitta savolga javob beradi: **"backend'da xatomi yoki kamerada?"**

| Bo'lim | Nima bor |
|---|---|
| Holat | muammoli kameralar birinchi, tizim ko'rsatkichlari |
| Kameralar | jadval: holat/hudud/kodek filtrlari, ustun saralash |
| Guruhlar | hudud / registrator / tugun kesimida uzilishlar reytingi |
| Tahlil | uzilishlar sutka bo'ylab, cho'qqi oyna, eng yomon kameralar |
| Devor | 2×2–4×4 setka, sub-oqimda, talab bo'yicha |
| Diagnostika | bitta kamera: KPI, signal zanjiri, 30 kunlik kalendar, uzilishlar va harakatlar jurnali |
| Tizim | MediaMTX tugunlari, fon vazifalari, disk |
| Hodisalar | SSE oqimi jonli |
| Qurilma qo'shish | IP + parol → skaner qolganini topadi |

Konsol o'zi yetarli: shriftlar `debug-ui/vendor/fonts/` da, CDN'ga
chiqmaydi — kameralar bilan bitta yopiq tarmoqda ham ishlaydi.

## Tizim qanday ishlaydi

```
Kamera (RTSP)
   ↓  MediaMTX to'g'ridan-to'g'ri tortadi (FFmpeg yo'q)
   ↓  Yo'l ko'rish so'ralganda API orqali yaratiladi — faylga yozilmaydi
   ↓  MediaMTX WebRTC va HLS'da tarqatadi
Brauzer — avval WebRTC, ishlamasa HLS

   Faqat ikki holatda FFmpeg qo'shiladi:
     · brauzer kameraning kodegini uddalay olmasa
     · "tez ochilsin" belgilangan bo'lsa (qisqa GOP uchun)
```

### Kameralar soni cheklanmagan

`mediamtx.yml` ichida kameralar ro'yxati **yo'q** — bitta shablon yo'l
bor. Yo'llar kimdir ko'rmoqchi bo'lganda yaratiladi va bo'shab qolgach
olib tashlanadi.

Bu shakl uchun emas, o'lchov uchun qilingan: MediaMTX har `paths/add`
so'roviga butun konfiguratsiyani qayta yuklaydi, ya'ni bitta yo'l
qo'shish narxi mavjud yo'llar soniga qarab o'sadi (0 yo'lda 9 ms, 2400
yo'lda 297 ms). 5000 kamerani oldindan ro'yxatga olish ~1,5–2 soat
olardi. Talab bo'yicha yaratishda:

| | Oldindan ro'yxat | Talab bo'yicha |
|---|---|---|
| Birinchi sinxron | ~1,5–2 soat | **0,0 s** |
| Tinch tsikl (30 s) | 21 HTTP so'rov | **6 ms** |
| Kamera ochilishi | 258 ms | **8,4 ms** |
| MediaMTX'dagi yo'llar | 10001 | ko'rilayotganlar soni |

5000 kamerada boshqa o'lchovlar (`docs/BENCHMARK.md`):

| Amal | Vaqt | Hajm |
|---|---|---|
| Kameralar ro'yxati (5000 ta) | 85 ms | 1202 KB |
| Bitta hudud (bbox bo'yicha) | 12 ms | — |
| Admin ro'yxati (100 tadan) | 7 ms | 54 KB |
| Uzilishlar reytingi (guruh) | 55 ms | 2 KB |
| `mediamtx.yml` | 1 ms | **36 qator** |

Resurs kameralar soniga emas, **ayni damda ko'rilayotganlar soniga**
qarab sarflanadi. `/health` dagi `managed` — hozir MediaMTX ro'yxatida
turgan yo'llar; u kamera soniga emas, tomoshabinlarga qarab o'sishi kerak.

### Ommaviy qo'shish

Boshqaruv panelidagi **NVR** tugmasi bitta registratordagi barcha
kanallarni birdaniga qo'shadi: manzil, login/parol va kanallar
oralig'ini (`1-64`) berasiz, tizim ularni **parallel** tekshiradi
(16 kanal ≈ 0,1 s) va faqat javob berganlarini saqlaydi.

### Kodeklar — o'girish deyarli hech qachon kerak emas

Zamonaviy brauzerlar H.265 (HEVC) ni ham o'qiy oladi. Sayt buni
ochilishda tekshiradi va imkoni bo'lsa oqimni **o'girmasdan** beradi.

Bitta 1440p kamerada o'lchangan:

| | Xom H.265 | H.265 → H.264 |
|---|---|---|
| GPU koder | **0%** | 4% |
| Xotira | 221 MB | 436 MB |
| Tarmoq | 1,4 Mbit/s | 2,5 Mbit/s |
| NVENC sessiyasi | **band qilmaydi** | 1 ta |

Xom oqim har jihatdan arzon. Eng muhimi — NVENC sessiyalarini band
qilmaydi: GeForce kartalarda ular 8 ta bilan cheklangan.

Qaysi yo'l tanlanishi brauzerga bog'liq:

| Brauzer | H.265 | Natija |
|---|---|---|
| Chrome / Edge (apparatli dekodlash bilan) | ha | o'girilmaydi |
| Safari (Mac, iPhone) | ha | o'girilmaydi |
| Firefox | yo'q | H.264 ga o'giriladi |

Pleyer **har doim avval WebRTC** ni sinaydi va faqat u ishlamasa HLS'ga
tushadi — kodekdan qat'i nazar. Brauzer "o'qiy olaman" desa-yu amalda
uddalay olmasa, sayt buni sezadi va zaxira yo'lga o'zi qaytadi.

> H.265 ni WebRTC orqali uzatish brauzer va apparatga bog'liq. Ishlab
> chiqarishga chiqarishdan oldin operatorlaringizning mashinalarida bir
> marta tekshirib ko'ring — natija `/health` dagi `open_ms` da ko'rinadi.

### Tezlik nimaga bog'liq

Ochilish vaqtining asosiy qismi — **kameradan keyframe kutish**. WebRTC
ham, HLS ham tasvirni faqat keyframe'dan boshlay oladi, kameralarda esa
u odatda har 2–4 soniyada bir marta yuboriladi.

8 ta real kamerada o'lchangan (WebRTC orqali):

| Kamera | Keyframe orasi | Ochilish |
|---|---|---|
| Chorsu | 4,0 s | 0,65 s |
| Amir Temur (tez rejim) | 1,2 s | 1,03 s |
| A3 | 2,0 s | 1,08 s |
| Labi Hovuz (tez rejim) | 1,2 s | 1,47 s |
| A2 | 2,0 s | 1,96 s |
| Registon | 4,0 s | 2,48 s |
| A1 (uzoq tarmoq) | — | 10,5 s |

Sayt bu kutishni ikki tomondan qisqartiradi (`core/fast_start.py`):

- **Surat darhol ko'rsatiladi.** Kamera bosilganda avval uning JPEG
  surati chiqadi (~0,2 s), video orqa fonda ulanadi.
- **Kameradan keyframe so'raladi** (ONVIF SetSynchronizationPoint,
  Hikvision'da ISAPI orqali ham) — tasvir GOP oxirini kutmasdan ~0,5 s
  da keladi. Qo'llamaydigan kamera jim rad etadi.

Ochilish vaqtining qaysi bosqichda ketayotgani taxmin qilinmaydi —
pleyer uni o'lchab yuboradi (`POST /api/v1/metrics/open`), `/health`
esa `open_ms` da p50/p95 qilib ko'rsatadi: `stream_ms` (backend va
MediaMTX), `signal_ms` (tarmoq), `frame_ms` (kameradagi keyframe
oralig'i). Qaysi biri katta bo'lsa — o'sha yerni tuzatish kerak.

**Yana tezlashtirishning uchta yo'li:**

1. **Registratorda `I Frame Interval` ni kamaytiring** (25 fps uchun 25;
   odatda 100 qo'yilgan). Bepul va hamma kameraga ta'sir qiladi.
2. **"Tez ochilsin"** belgisi — oqim doim tayyor turadi. Narxi: ~200 MB
   va bir oz GPU.
3. Ko'rilgan kameraning yo'li 30 daqiqa ro'yxatda qoladi — yopib qayta
   ochilsa MediaMTX'da yo'l yaratish kerak bo'lmaydi.

### Tarmoq — eng katta chegara

Lokal kameralar (1 ms) va uzoq kameralar (47–50 ms) o'rtasidagi farq
o'lchovda yaqqol ko'rindi: uzoqdagilarda RTP paketlar yo'qolib, ochilish
10 soniyagacha cho'zildi.

Kameralar bir necha manzilda bo'lsa, **har bir joyga alohida MediaMTX
qo'ying** (`/api/v1/admin/nodes`) va sayt kerakli tugunga yo'naltirsin.
Kamera trafigi lokal tarmoqda qoladi, magistralga faqat ko'rilayotgan
oqim chiqadi.

## Kod tuzilishi

Backend (web-qatlam) va MediaMTX qatlami ataylab ajratilgan — bir-biri
bilan faqat `from media import sync` chegarasi orqali gaplashadi:

```
main.py                  kirish nuqtasi: CLI, bootstrap, uvicorn
api/                     BACKEND (web-qatlam)
  ├─ __init__.py         create_app() — ilovani yig'ish
  ├─ config.py           portlar, RTSP shablonlari (muhit o'zgaruvchilari)
  ├─ models.py           so'rov modellari (Pydantic)
  ├─ deps.py             yagona kirish (X-API-Key)
  ├─ helpers.py          baza qatori → mijoz/MediaMTX ko'rinishlari
  ├─ bootstrap.py        birinchi ishga tushirish, admin yaratish
  ├─ auth.py             /auth/*      — stream chipta + konsol login
  ├─ cameras.py          /cameras/*   — ro'yxat, holat, oqim, surat
  ├─ streams.py          /streams     — batch oqim chiptalari
  ├─ events.py           /events      — SSE holat o'zgarishlari
  ├─ devices.py          /devices/*   — skan (SSE), pasport
  ├─ nodes.py            /admin/nodes — MediaMTX tugunlari
  ├─ analytics.py        /admin/uptime, /admin/outages/hourly,
  │                      /admin/cameras/{ref}/history — uzilishlar tahlili
  ├─ metrics.py          /metrics/open — pleyer o'lchagan ochilish vaqti
  ├─ health.py           /health — salomatlik + egress
  └─ admin.py            /admin/*     — CRUD, NVR import, foydalanuvchilar
media/                   MEDIAMTX QATLAMI
  ├─ sync.py             konfiguratsiya, jonli API, FFmpeg buyruqlari
  ├─ reconciler.py       fon: MediaMTX'ni tirik tutish, yo'llarni
  │                      kelishtirish, muzlagan oqimni aniqlash
  └─ launcher.py         talab bo'yicha o'girish jarayoni
core/                    UMUMIY INFRATUZILMA
  ├─ db.py               SQLite sxemasi va migratsiya
  ├─ security.py         admin paroli (scrypt), kamera parollari (Fernet),
  │                      oqim chiptalari
  ├─ health.py           kameralar tirikligini fonda kuzatish (TCP)
  ├─ snapshots.py        suratlarni diskda pog'onali yangilash
  ├─ rtsp_probe.py       kamerani tekshirish: tarmoq, login, kodek, SDP
  ├─ device_info.py      qurilma pasporti (ONVIF / ISAPI)
  ├─ fast_start.py       JPEG surat va keyframe so'rash
  ├─ bus.py              jarayon ichidagi pub/sub (SSE uchun)
  ├─ events.py           hodisalar jadvali (yozish va tozalash)
  ├─ metrics.py          ochilish vaqti namunalari (p50/p95)
  └─ log.py              strukturali jurnal (nigoh.log, JSON satrlar)
debug-ui/                DIAGNOSTIKA KONSOLI (ENABLE_UI=1)
  ├─ index.html          sahifa tuzilishi
  ├─ style.css           barcha uslublar
  ├─ app.js              konsol: jadval, devor, tahlil, pleyer
  └─ vendor/             hls.js va shriftlar (CDN'siz ishlashi uchun)
tests/                   pytest — `pytest` (pytest.ini: testpaths=tests)
deploy/                  nginx konfiguratsiyasi va yangilash skripti
scripts/
  ├─ import_mediamtx.py  qo'lda yozilgan mediamtx.yml ni bazaga ko'chirish
  └─ qabul_test.py       qabul tekshiruvi (jonli serverga qarshi)
stream_launcher.py       MediaMTX chaqiradigan yupqa qobiq (ildizda shart)
mediamtx/                MediaMTX'ning o'zi — yuklab olinadi, git'da yo'q
```

Ma'lumot fayllari (`cameras.db`, `secret.key`, `mediamtx.yml`, loglar,
`snapshots/`) `NIGOH_DATA` katalogida; standart — loyiha ildizi,
konteynerda `/data` volume.

## Maxfiylik

Bu fayllar **hech qachon** repozitoriyga tushmasligi kerak
(`.gitignore` da):

- `secret.key` — kamera parollarini ochadigan kalit
- `cameras.db` — kameralar va shifrlangan parollar
- `mediamtx.yml` — ichida **ochiq** RTSP login/parollar
- `.env` — API kaliti va admin paroli

Kamera parollari bazada shifrlangan holda yotadi va brauzerga hech
qachon qaytarilmaydi — admin panelida ham faqat `•••` ko'rinadi.

## Boshqa tarmoqdan ochish

Servis `0.0.0.0` da tinglaydi. Brauzer MediaMTX'ga ham to'g'ridan
ulanadi, shuning uchun firewall'da ochilishi kerak:

| Port | Protokol | Nima |
|---|---|---|
| 8010 | tcp | API va konsol |
| 8888 | tcp | HLS video |
| 8889 | tcp | WebRTC signal (WHEP) |
| **8189** | **udp va tcp** | WebRTC media (ICE) |

8189 ikkala protokolda ochilsin: WebRTC avval UDP'ni sinaydi, korporativ
tarmoqda UDP yopiq bo'lsa ICE TCP zaxirasi ishga tushadi. Ikkalasi ham
yopiq bo'lsa brauzer HLS'ga tushadi — ishlaydi, lekin sekinroq.

MediaMTX boshqa kompyuterda bo'lsa:

```powershell
$env:MEDIA_HOST = "192.168.1.50"; venv\Scripts\python.exe main.py
```

## Keyingi qadamlar

- Uzilish **sababini** kamera kesimida saqlash (hozir faqat vaqti bor)
- Kamera ko'payganda SQLite o'rniga PostgreSQL
- Kameralarni yozib borish (MediaMTX `record: yes`)

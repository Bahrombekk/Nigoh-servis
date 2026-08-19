# Nigoh — kamera xaritasi

Xaritaga biriktirilgan IP kameralarni kuzatish tizimi. Markerga bosilganda
o'sha hududdagi kameralar ro'yxati ochiladi va tanlangan kameraning jonli
tasviri ko'rsatiladi. Kameralar super-admin panelidan — IP, login, parol
kiritib — qo'shiladi.

Servis sifatida ishlatish uchun hujjatlar:

| Kim uchun | Fayl |
|---|---|
| Serverga qo'yuvchi | [docs/DEPLOY.md](docs/DEPLOY.md) — Docker, portlar, HTTPS, zaxira |
| Backendchi | [docs/BACKEND.md](docs/BACKEND.md) — API kontrakt, auth, integratsiya |
| Frontendchi | [docs/FRONTEND.md](docs/FRONTEND.md) — endpointlar, tayyor player kodi |

## Ishga tushirish

Serverda (Docker):

```bash
cp .env.example .env       # ADMIN_PAROL ni to'ldiring
docker compose up -d --build
```

Windows'da (lokal ishlab chiqish) eng oson yo'l: **`ishga-tushirish.bat`**
faylini ikki marta bosing. U MediaMTX va saytni birga ishga tushiradi,
brauzerni ochadi.

Qo'lda:

```powershell
venv\Scripts\python.exe -m pip install -r requirements.txt
start mediamtx\mediamtx.exe mediamtx.yml     # video oqimlar
venv\Scripts\python.exe main.py              # sayt
```

Brauzerda: **http://localhost:8010**

Port band bo'lsa (Windows'da 8000/8080 ni ko'pincha Docker Desktop yoki tizim
xizmatlari egallaydi — `WinError 10013` shundan chiqadi):

```powershell
$env:PORT = "8020"; venv\Scripts\python.exe main.py
```

## Super-admin

Birinchi ishga tushirishda admin yaratiladi va paroli konsolga chiqadi.
Parolni istalgan payt almashtirish mumkin:

```powershell
venv\Scripts\python.exe main.py --admin-parol YangiParol
```

Login sahifadagi **"Super admin"** tugmasi orqali. Kirgach chapda boshqaruv
paneli ochiladi: kamera qo'shish, tahrirlash, ulanishni tekshirish, o'chirish.

## Kamera qo'shish

1. **"+ Yangi kamera"** → shakl ochiladi.
2. Nomi va hududini yozing.
3. **"Xaritadan tanlash"** → xaritada joyni bosing (yoki koordinatani qo'lda kiriting).
4. Ishlab chiqaruvchini tanlang — RTSP yo'li avtomatik to'ldiriladi.
5. IP, login va parolni kiriting.
6. **"Ulanishni tekshirish"** — kamera javob beryaptimi, parol to'g'rimi va
   qaysi kodekda ishlayotganini aytadi.
7. **Saqlash** → keyin **"MediaMTX"** tugmasi orqali oqimlarni qo'llang.

Hudud nomi bir xil yozilgan kameralar bitta guruh hisoblanadi.

## Interfeys bo'limlari

Pastki markazdagi dock orqali (yoki to'g'ridan-to'g'ri havola bilan):

| Bo'lim | Havola | Nima bor |
|---|---|---|
| Xarita | `/` | klaster markerlar, qidiruv (Ctrl K), jonli ko'rish paneli |
| Video devor | `/#wall` | 2×2–4×4 setka, hudud filtri, sahifalash, avto-aylanish, kadr rejimi, plitka to'liq ekranı, surat yuklab olish |
| Dashboard | `/#dash` | holat donut'i, ochilish sparkline'i, hudud/texnik kesimlar, hodisalar — har 15 s yangilanadi |
| Boshqaruv | `/#admin` | jadval: holat/hudud/kodek/rejim filtrlari, ustun saralash, NVR import, skaner |

## API — tashqi mijozlar uchun

Nigoh servis sifatida ishlaydi: frontend'chilar `/api/v1/...` bilan quradi.
Interaktiv hujjat serverning o'zida: **`/docs`** (Swagger) va **`/redoc`**.

| Bo'lim | Prefiks | Kirish |
|---|---|---|
| Kameralar (xarita, oqim, surat) | `/api/v1/cameras` | ochiq |
| Dashboard tarixi | `/api/v1/stats` | ochiq |
| Kirish/chiqish | `/api/v1/auth` | — |
| Boshqaruv (CRUD, NVR, skaner, tugunlar) | `/api/v1/admin` | sessiya |

Har bir kamerada yagona **`state`** maydoni bor:
`disabled` (admin o'chirgan) · `unknown` (hali tekshirilmagan) ·
`offline` (tarmoqdan javob yo'q) · `stalled` (port ochiq, tasvir kelmayapti) ·
`online`. Probe kamera **kodeki bilan birga o'lchami, FPS va audio**
borligini ham qaytaradi.

### Rollar

Ikki rol bor: **admin** hammasini ko'radi va boshqaradi; **operator**
faqat o'ziga biriktirilgan hududlardagi kameralarni ko'radi (xarita
ro'yxati, oqim va surat shu ro'yxat bilan cheklanadi). Operatorlar
`/api/v1/admin/users` orqali yaratiladi:

```json
{"username": "operator1", "password": "...", "role": "operator",
 "regions": ["Toshkent", "Buxoro"]}
```

Kirish yagona: barcha API `X-API-Key` talab qiladi (`NIGOH_API_KEY`
majburiy — busiz servis ishga tushmaydi). Debug UI (`ENABLE_UI=1`,
standart) cookie login bilan ishlaydi.

### Tugun salomatligi

`/api/v1/admin/nodes` har tugun uchun `status` (`online` — API tirik va
muzlagan oqim yo'q; `degraded` — kamida bitta faol oqim muzlagan;
`offline` — API javob bermayapti) va ish ko'rsatkichlarini qaytaradi:
sozlangan/tayyor yo'llar, tomoshabinlar soni, o'tgan trafik. Xuddi shu
qisqartma `/api/v1/admin/status` da ham bor.

Eski `/api/...` manzillari ham xuddi shu endpointlarga olib boradi (ichki
test interfeys va MediaMTX auth uchun saqlangan), lekin hujjatda faqat v1.

Fon xizmatlari hodisalarni **`nigoh.log`** ga JSON satrlar bilan yozadi
(aylanma, 5 MB × 3) — keyinchalik Loki/OpenSearch'ga ulash mumkin.

## Tizim qanday ishlaydi

```
Kamera (RTSP)
   ↓  MediaMTX to'g'ridan-to'g'ri tortadi (FFmpeg yo'q)
   ↓  Yo'l API orqali ro'yxatga olinadi — faylga yozilmaydi
   ↓  MediaMTX WebRTC va HLS'da tarqatadi
Brauzer — WebRTC (H.265 ni ham o'qiydi), ishlamasa HLS

   Faqat ikki holatda FFmpeg qo'shiladi:
     · brauzer H.265 ni uddalay olmasa
     · "tez ochilsin" belgilangan bo'lsa (qisqa GOP uchun)
```

### Kameralar soni cheklanmagan

`mediamtx.yml` ichida kameralar ro'yxati **yo'q**. Bitta shablon yo'l bor,
u chaqirilganda `stream_launcher.py` bazadan kerakli kamerani topadi.

1000 ta kamera bilan o'lchangan:

| Amal | Vaqt | Hajm |
|---|---|---|
| Xarita ro'yxati (1000 ta) | 37 ms | 91 KB |
| Bitta hudud (bbox bo'yicha) | 14 ms | 0,9 KB |
| Admin ro'yxati (100 tadan) | 6 ms | 54 KB |
| Qidiruv | 4 ms | — |
| `mediamtx.yml` | 1 ms | **36 qator** |

Konfiguratsiya 3 ta kamerada ham, 1000 tada ham 36 qator — shuning uchun
yangi kamera qo'shilganda MediaMTX'ni qayta ishga tushirish **shart emas**,
u darhol ishlaydi.

Resurs kameralar soniga emas, **ayni damda ko'rilayotganlar soniga** qarab
sarflanadi. 1000 ta kamera bo'lib, 3 kishi ko'rayotgan bo'lsa — 3 ta oqim
ishlaydi, qolgan 997 tasi hech narsa yemaydi.

### Ommaviy qo'shish

1000 ta kamerani qo'lda kiritib bo'lmaydi. Boshqaruv panelidagi **NVR**
tugmasi bitta registratordagi barcha kanallarni birdaniga qo'shadi:
manzil, login/parol va kanallar oralig'ini (`1-64`) berasiz, tizim ularni
**parallel** tekshiradi (16 kanal ≈ 0,1 s) va faqat javob berganlarini
saqlaydi. Nuqtalar bir-birini bosmasligi uchun spiral bo'ylab tarqatiladi;
keyin har birini xaritada aniq joyiga surish mumkin.

### Kodeklar — o'girish deyarli hech qachon kerak emas

Zamonaviy brauzerlar H.265 (HEVC) ni ham o'qiy oladi. Sayt buni ochilishda
tekshiradi va imkoni bo'lsa oqimni **o'girmasdan** beradi.

Bitta 1440p kamerada o'lchangan:

| | Xom H.265 | H.265 → H.264 |
|---|---|---|
| GPU koder | **0%** | 4% |
| Xotira | 221 MB | 436 MB |
| Tarmoq | 1,4 Mbit/s | 2,5 Mbit/s |
| NVENC sessiyasi | **band qilmaydi** | 1 ta |

Xom oqim har jihatdan arzon. Eng muhimi — NVENC sessiyalarini band
qilmaydi: GeForce kartalarda ular 8 ta bilan cheklangan, ya'ni o'girish
bilan bir vaqtda 8 tadan ortiq kamerani ko'rib bo'lmasdi. Xom oqimda bunday
chegara yo'q.

Qaysi yo'l tanlanishi brauzerga bog'liq:

| Brauzer | H.265 | Natija |
|---|---|---|
| Chrome / Edge (Windows, apparatli dekodlash) | ha | o'girilmaydi |
| Safari (Mac, iPhone) | ha | o'girilmaydi |
| Firefox | yo'q | H.264 ga o'giriladi |

O'girish faqat zaxira sifatida qoladi. Brauzer "o'qiy olaman" desa-yu amalda
uddalay olmasa, sayt buni sezadi va o'zi o'girilgan oqimga qaytadi.

Bir nuance: xom H.265 faqat HLS orqali beriladi (WebRTC H.265 ni bilmaydi),
shuning uchun ochilishi ~1 soniya sekinroq. Tezlik muhim bo'lgan kameralarda
"doim tayyor" ni yoqing yoki NVR'da H.264 ga o'ting — u holda WebRTC ham,
o'girishsiz uzatish ham birga ishlaydi.

### Tezlik nimaga bog'liq

Ochilish vaqtining asosiy qismi — **kameradan keyframe kutish**. WebRTC ham,
HLS ham tasvirni faqat keyframe'dan boshlay oladi, kameralarda esa u odatda
har 2–4 soniyada bir marta yuboriladi.

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

Xom oqimda vaqt tasodifiy: keyframe siklining qayeriga tushishingizga
bog'liq. O'girilgan oqimda GOP 1,2 s bo'lgani uchun barqaror.

Sayt bu kutishni ikki tomondan qisqartiradi (`fast_start.py`):

- **Surat darhol ko'rsatiladi.** Kamera bosilganda avval uning JPEG surati
  chiqadi (~0,2 s), video orqa fonda ulanadi. Ochilish bir zumda his
  qilinadi. Suratlar 8 soniya keshlanadi — kamera bosim ostida qolmaydi.
- **Kameradan keyframe so'raladi.** Ko'rish boshlanganda kameraga "hozir
  keyframe yubor" buyrug'i ketadi (ONVIF SetSynchronizationPoint, Hikvision'da
  ISAPI orqali ham) — tasvir GOP oxirini kutmasdan ~0,5 s da keladi.
  Qo'llamaydigan kamera jim rad etadi, hech narsa buzilmaydi.

**Yana tezlashtirishning uchta yo'li** — samaradorlik bo'yicha:

1. **Registratorda `I Frame Interval` ni kamaytiring** (25 fps uchun 25;
   odatda 100 qo'yilgan). Bepul va hamma kameraga ta'sir qiladi.
2. **"Tez ochilsin"** belgisi — oqim doim tayyor turadi va qisqa GOP bilan
   tayyorlanadi. ~2,5 s o'rniga ~1 s. Narxi: ~200 MB va bir oz GPU.
3. Sayt kamera nomiga sichqoncha kelganda oqimni (va suratni) jimgina
   oldindan tayyorlaydi.

### Tarmoq — eng katta chegara

Lokal kameralar (1 ms) va uzoq kameralar (47–50 ms) o'rtasidagi farq
o'lchovda yaqqol ko'rindi: uzoqdagilarda RTP paketlar yo'qolib, ochilish
10 soniyagacha cho'zildi.

Kameralar bir necha manzilda bo'lsa, **har bir joyga alohida MediaMTX
qo'ying** va sayt kerakli tugunga yo'naltirsin. Kamera trafigi lokal
tarmoqda qoladi, magistralga faqat ko'rilayotgan oqim chiqadi.

## Kod tuzilishi

Backend (web-qatlam) va MediaMTX qatlami ataylab ajratilgan — bir-biri
bilan faqat `from media import sync` chegarasi orqali gaplashadi:

```
main.py                  kirish nuqtasi: CLI, bootstrap, uvicorn
app/                     BACKEND (web-qatlam)
  ├─ __init__.py         create_app() — ilovani yig'ish, static
  ├─ config.py           portlar, RTSP shablonlari (muhit o'zgaruvchilari)
  ├─ models.py           so'rov modellari (Pydantic)
  ├─ helpers.py          baza qatori → brauzer/MediaMTX ko'rinishlari
  ├─ bootstrap.py        birinchi ishga tushirish, admin yaratish
  ├─ routes_auth.py      /api/auth/*    — kirish/chiqish
  ├─ routes_public.py    /api/cameras/* — xarita, oqim, surat (kirishsiz)
  └─ routes_admin.py     /api/admin/*   — CRUD, NVR import, skaner, MediaMTX
media/                   MEDIAMTX QATLAMI
  ├─ sync.py             mediamtx.yml yaratish, jonli API, FFmpeg buyruqlari
  └─ launcher.py         talab bo'yicha o'girish jarayoni
core/                    UMUMIY INFRATUZILMA (ikkala qatlam ishlatadi)
  ├─ db.py               SQLite sxemasi va migratsiya
  ├─ security.py         admin paroli (scrypt), kamera parollari (Fernet)
  ├─ health.py           kameralar tirikligini fonda kuzatish
  ├─ rtsp_probe.py       kamerani tekshirish: tarmoq, login, kodek, o'lcham
  ├─ log.py              strukturali jurnal (nigoh.log, JSON satrlar)
  └─ fast_start.py       JPEG surat (poster) va keyframe so'rash
scripts/
  └─ import_mediamtx.py  qo'lda yozilgan mediamtx.yml ni bazaga ko'chirish
stream_launcher.py       MediaMTX chaqiradigan yupqa qobiq (ildizda turishi shart)
mediamtx/                MediaMTX'ning o'zi (exe) — yuklab olinadi, git'da yo'q
static/
  ├─ index.html          sahifa tuzilishi
  ├─ style.css           barcha uslublar
  ├─ app.js              xarita, player, video devor, dashboard, boshqaruv
  └─ uz.geojson          O'zbekiston chegarasi (OSM)
```

Ildizda qoladigan ma'lumot fayllari (git'ga tushmaydi): `cameras.db`,
`secret.key`, `mediamtx.yml`, `auto.crt/key` — yo'llari kod ko'chganda ham
o'zgarmasligi uchun ataylab ildizda.

## Maxfiylik

Bu fayllar **hech qachon** repozitoriyga tushmasligi kerak (`.gitignore` da):

- `secret.key` — kamera parollarini ochadigan kalit
- `cameras.db` — kameralar va shifrlangan parollar
- `mediamtx.yml` — ichida **ochiq** RTSP login/parollar (MediaMTX shunday talab qiladi)

Kamera parollari bazada shifrlangan holda yotadi va brauzerga hech qachon
qaytarilmaydi — admin panelida ham faqat `•••` ko'rinadi.

## Boshqa tarmoqdan ochish

Sayt `0.0.0.0` da tinglaydi, ya'ni LAN'dagi boshqa qurilmalar
`http://SERVER_IP:8010` orqali kira oladi. Brauzer MediaMTX'ga ham to'g'ridan
ulanadi (8888 va 8889-portlar), shuning uchun ular ham ochiq bo'lsin.

MediaMTX boshqa kompyuterda bo'lsa:

```powershell
$env:MEDIA_HOST = "192.168.1.50"; venv\Scripts\python.exe main.py
```

## Keyingi qadamlar

- HTTPS (nginx + sertifikat) — parollar ochiq tarmoqdan o'tmasligi uchun
- Kamera ko'payganda SQLite o'rniga PostgreSQL
- Kameralarni yozib borish (MediaMTX `record: yes`)

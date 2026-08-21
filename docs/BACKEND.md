# Nigoh — backendchi uchun qo'llanma

> Bu hujjat **modelni** tushuntiradi: kim nimani yuritadi, video qanday
> yetib boradi, nimani kuzatish kerak. Endpointlarning to'liq ro'yxati va
> so'rov namunalari — [INTEGRATION.md](INTEGRATION.md) da.

Bu hujjat kameralar bilan **hech qachon ishlamagan** backendchi uchun.
Nigoh — tayyor kamera mikroservisi: siz uni o'z tizimingizga oddiy REST
servis sifatida ulaysiz, kamera protokollari (RTSP, kodeklar, oqimlar)
uning ichida qoladi.

Interaktiv API hujjati: **`http://SERVER:8010/docs`** (Swagger) va `/redoc`.

## Servis nima qiladi (1 daqiqada)

```
Sizning tizimingiz ──REST──▶ Nigoh (8010)
                                │  boshqaruv qatlami: kameralar bazasi,
                                │  auth, rollar, monitoring
                                ▼
                             MediaMTX ──▶ brauzerga video (8888/8889)
                                ▲
                                └── IP kameralar (RTSP)
```

- **Siz gaplashadigan yagona narsa — 8010-portdagi REST API.**
- MediaMTX — ichki video dvijok. Uning API'si (9997) faqat 127.0.0.1 da,
  unga to'g'ridan-to'g'ri murojaat qilmang: Nigoh uni o'zi boshqaradi
  (har 30 soniyada bazadagi holat bilan kelishtiradi, yiqilsa qayta
  ko'taradi — bunga aralashish shart emas).
- Video trafik sizning backend orqali **o'tmaydi** — brauzer media
  portlariga to'g'ridan ulanadi. Sizning API faqat metadata beradi.

## API tuzilishi

Hammasi `/api/v1` ostida, resource-based:

| Prefiks | Kirish | Nima bor |
|---|---|---|
| `/api/v1/cameras` | X-API-Key | ro'yxat (bbox filtri), holat, oqim manzili, surat |
| `/api/v1/streams` | X-API-Key | batch oqim chiptalari (128 tagacha) |
| `/api/v1/events` | X-API-Key | SSE — holat o'zgarishlari jonli |
| `/api/v1/devices` | X-API-Key | qurilma skani (SSE), pasport |
| `/api/v1/admin` | X-API-Key | kameralar CRUD, NVR import, tugunlar, holat, hodisalar |
| `/api/v1/admin/uptime`, `/admin/outages/hourly`, `/admin/cameras/{ref}/history` | X-API-Key | uzilishlar tahlili |
| `/api/v1/metrics/open` | X-API-Key | pleyer o'lchagan ochilish vaqti |
| `/api/v1/auth` | — | `/auth/stream` ni MediaMTX chaqiradi; login — faqat konsol uchun |
| `/health` | ochiq | Docker HEALTHCHECK va monitoring |

**Rollar yo'q.** Kirish yagona: `X-API-Key`. Kim nimani ko'rishi
kerakligini o'z tizimingiz hal qiladi.

Eski `/api/...` manzillari ham ishlaydi (ichki test UI uchun), lekin yangi
integratsiyada faqat `/api/v1` ni ishlating.

## Asosiy integratsiya modeli: o'z tizimingiz + Nigoh

Sizda o'z backend/frontend'ingiz, o'z foydalanuvchi va rol tizimingiz bor.
Nigoh bunga **aralashmaydi** — u faqat kamera mikroservisi. Tavsiya
etiladigan sxema:

```
Foydalanuvchi ─▶ Sizning frontend ─▶ Sizning backend (o'z rollaringiz)
                                          │ X-API-Key
                                          ▼
                                        NIGOH
                                          ▼
                    MediaMTX ──▶ video TO'G'RIDAN brauzerga (chipta bilan)
```

Sozlash (Nigoh tomonda, `.env`):

```
NIGOH_API_KEY=<uzun tasodifiy kalit>   # majburiy, usiz servis ko'tarilmaydi
ENABLE_UI=0                            # ishlab chiqarishda konsol yopiq
```

Sizning backend har so'rovga `X-API-Key: <kalit>` qo'shadi va **to'liq**
kiradi — login, cookie, sessiya kerak emas.

### Video qanday yetib boradi (muhim!)

Video oqimi sizning backend orqali **o'tmaydi** (media trafikni proksilash
og'ir va keraksiz). O'rniga chipta uzatiladi:

```
1. Frontend'ingiz:  "kamera X ni ochmoqchiman" → sizning backend
2. Sizning backend: O'Z rolingiz bo'yicha ruxsatni tekshiradi
3. Ruxsat bo'lsa:   GET nigoh:8010/api/v1/cameras/{id}/stream  (X-API-Key bilan)
                    → {"webrtc_url": "...?token=...", "stream_url": "..."}
4. Shu javobni frontend'ingizga qaytarasiz
5. Brauzer chiptali manzil bilan videoni MediaMTX'dan to'g'ridan oladi
```

Chipta 1 soat yashaydi va faqat shu kameraga ishlaydi — manzilni bilgan
begona odam ham chiptasiz videoni ocholmaydi. Shu sababli oqim manzilini
**keshlamang** — har ochilishda 3-qadamni qaytaring (bu arzon so'rov).

### Metadata kimda turadi

Kamera nomi, kategoriyasi, xaritadagi o'rni kabi narsalarni **o'z
bazangizda** yuritishingiz mumkin — o'z jadvalingizda `nigoh_camera_id`
ustuni bilan bog'lang. Nigoh'dagi `name/region/lat/lng` maydonlarini
xohlasangiz ishlatasiz (u yerda ham bor), xohlamasangiz e'tiborsiz
qoldirasiz — Nigoh uchun majburiysi ulanish ma'lumotlari (IP, parol,
yo'l) xolos.

Nigoh'ga o'z ID'ingiz bilan murojaat qilishingiz ham mumkin: kamera
yaratishda `external_id` bering va keyin hamma joyda `12` o'rniga
`ext:cam-toshkent-014` deb yozing — mapping jadval kerak emas.

Namuna (sizning backend'ingizda):

```python
import requests

NIGOH = "http://nigoh:8010/api/v1"
H = {"X-API-Key": "<kalit>"}

# kamera qo'shish (sizning admin panelingizdan kelgan ma'lumot bilan)
cam = requests.post(f"{NIGOH}/admin/cameras", headers=H, json={
    "name": "Ombor 1", "region": "Toshkent", "lat": 41.31, "lng": 69.28,
    "ip": "192.168.1.10", "username": "admin", "password": "...",
    "vendor": "hikvision", "rtsp_path": "/Streaming/Channels/101",
}).json()
# cam["id"] ni o'z bazangizga saqlang

# foydalanuvchi ko'rmoqchi bo'lganda (o'z ruxsatingizni tekshirib bo'lib):
urls = requests.get(f"{NIGOH}/cameras/{cam['id']}/stream", headers=H).json()
# urls ni frontend'ga qaytaring
```

## Autentifikatsiya modeli

Bitta mexanizm: har so'rovda `X-API-Key: <NIGOH_API_KEY>`. Kalit
bo'lmasa yoki noto'g'ri bo'lsa — `401`. Kalit `.env` da turadi va
**servis usiz umuman ishga tushmaydi** (fail fast: himoyasiz qolgandan
ko'ra ko'tarilmagani yaxshi).

Ikkita istisno:

- `GET /health` — ochiq, ichida sir yo'q (Docker HEALTHCHECK uchun).
- `POST /api/v1/auth/stream` — uni **MediaMTX** chaqiradi, mijoz emas:
  har bir tomosha so'roviga chipta tekshiriladi.

`/api/v1/auth/login` va foydalanuvchilar (`/admin/users`) faqat
**diagnostika konsoli** uchun (`ENABLE_UI=1`). Ishlab chiqarishda konsol
o'chiq bo'ladi va bu endpointlar umuman ro'yxatga olinmaydi.

**Oqim xavfsizligi haqida bilib qo'ying:** video portlari (8888/8889) ham
himoyalangan — MediaMTX har bir tomosha so'rovini Nigoh'dan tekshirtiradi.
`/api/v1/cameras/{id}/stream` bergan manzil ichida 1 soatlik imzoli chipta
bo'ladi. Shu sababli oqim manzillarini **keshlab bo'lmaydi** — har ochishda
yangisini so'rang.

## Kamera qo'shish yo'llari

Uchta yo'l bor, uchalasi ham parolni shifrlab saqlaydi va MediaMTX'ga
o'zi ulaydi (restart yo'q):

1. **Bitta kamera:** `POST /api/v1/admin/cameras` — IP, login, parol,
   vendor. Servis saqlashdan oldin kamerani o'zi tekshiradi (kodek,
   o'lcham aniqlanadi).
2. **NVR/registrator (ommaviy):** `POST /api/v1/admin/nvr/import` —
   bitta qurilmadagi 16–64 kanal birdaniga, parallel tekshiruv bilan.
   Avval `"dry_run": true` bilan chaqirib natijani ko'ring.
3. **Skaner:** `POST /api/v1/admin/scan` — IP+login yetadi, servis
   ishlab chiqaruvchini va jonli kanallarni o'zi topadi.

Tekshirish alohida ham bor: `POST /api/v1/admin/probe` — kamera javob
beryaptimi, parol to'g'rimi, kodek/o'lcham/FPS qanday.

## Monitoring — nimani kuzatish kerak

| Endpoint | Nima beradi |
|---|---|
| `GET /api/v1/admin/status` | bir qarashda: MediaMTX tirikmi, health sweep, muzlagan oqimlar, tugunlar holati |
| `GET /api/v1/admin/nodes` | har tugun: `status` (`online/degraded/offline`), tayyor oqimlar, tomoshabinlar, trafik |
| `GET /api/v1/admin/events` | media hodisalari: oqim muzladi/tiklandi, MediaMTX qayta ko'tarildi |
| har kamerada `state` | `online / offline / stalled / unknown / disabled` |

Holat o'zgarishlarini **poll qilish shart emas**: `GET /api/v1/events`
SSE ulanishi ularni o'zi yetkazadi (`online` / `offline` / `stalled`).
Boshlang'ich holatni ulanishdan oldin `/cameras/status` dan oling.
Webhook hozircha yo'q.

Uzilishlar tarixini tahlil qilish uchun alohida bo'lim bor:
`/admin/uptime?group_by=nvr` qaysi registrator aybdorligini,
`/admin/outages/hourly` esa qaysi soatda buzilayotganini aytadi.

Loglar: `/data/nigoh.log` — JSON satrlar
(`{"ts", "level", "service", "event", ...}`), Loki/OpenSearch'ga
to'g'ridan yuborsa bo'ladi. Prometheus metrics: konteyner ichida
`127.0.0.1:9998/metrics` (MediaMTX'niki).

## Ma'lumotlar qayerda

Hammasi `/data` volume'ida (compose'da `./data`):

| Fayl | Nima | Ehtiyot |
|---|---|---|
| `cameras.db` | SQLite: kameralar, foydalanuvchilar, hodisalar | zaxiralang |
| `secret.key` | kamera parollarini ochadigan kalit | **yo'qolsa parollar tiklanmaydi**; zaxiralang, hech kimga bermang |
| `mediamtx.yml` | avto-yaratiladi | qo'lda tahrirlamang — qayta yoziladi |
| `nigoh.log`, `mediamtx.log` | loglar (aylanma) | — |

Bazaga to'g'ridan-to'g'ri SQL bilan yozmang — API orqali ishlang, aks
holda MediaMTX bilan sinxronlik buziladi (o'qish mumkin, lekin sxema
o'zgarishi mumkinligini hisobga oling).

## O'zingizning servisingizga ulash namunasi

Nigoh'ni gateway ortiga oddiy upstream sifatida qo'ying:

```python
import requests

s = requests.Session()
s.headers["X-API-Key"] = "<kalit>"          # login/cookie kerak emas
BASE = "http://nigoh:8010/api/v1"

# kameralar ro'yxati
cams = s.get(f"{BASE}/cameras").json()["cameras"]

# salomatlik — o'z monitoringingizga qo'shing
status = s.get(f"{BASE}/admin/status").json()
assert status["mediamtx"], "video dvijok yiqilgan!"
assert all(n["pending_paths"] == 0 for n in status["nodes"]),     "MediaMTX'da ortiqcha yo'llar qolgan — uni qayta ishga tushiring"
```

## Muhit o'zgaruvchilari

To'liq ro'yxat izohlari bilan: **`.env.example`**. Eng muhimlari:

| O'zgaruvchi | Nima uchun |
|---|---|
| `NIGOH_API_KEY` | **majburiy** — usiz servis ko'tarilmaydi |
| `ENABLE_UI` | diagnostika konsoli (standart: `0`, o'chiq) |
| `MEDIA_HOST` / `MEDIA_BASE` | server NAT, domen yoki HTTPS proksi ortida bo'lsa |
| `WEBRTC_HOSTS` | brauzerga yuboriladigan qo'shimcha manzillar (NAT/konteyner) |
| `ADMIN_PAROL` | konsol admini (birinchi ishga tushishda) |

`.env` faylni **Python o'qimaydi** — uni `docker compose` yuklaydi.
Konteynersiz ishga tushirsangiz o'zgaruvchilarni o'zingiz bering.

## Nimalarga tegmaslik kerak

- MediaMTX API (9997) va uning konfiguratsiyasi — Nigoh o'zi boshqaradi.
- `secret.key` va `cameras.db` sxemasi.
- Oqim chiptalari formati — ichki mexanizm, o'zingiz yasamang.

# Nigoh — asosiy tizimga ulash qo'llanmasi

Bu hujjat Nigoh'ni o'z tizimiga ulaydigan dasturchi uchun. Nigoh —
media control plane: **"ID ber — oqim va holat qaytaraman"**. Xarita,
rollar, dashboard va foydalanuvchi boshqaruvi sizning tizimingizda
qoladi; Nigoh RTSP, kodeklar, MediaMTX, oqim chiptalari, kamera holati
va suratlar bilan shug'ullanadi.

Interaktiv hujjat: `http://SERVER:8010/docs` (Swagger).

## Kirish

Hamma so'rovda bitta sarlavha:

```
X-API-Key: <NIGOH_API_KEY qiymati>
```

Kalit serverdagi `.env` da turadi (usiz servis ishga tushmaydi).
Kalitsiz yoki noto'g'ri kalitli har qanday so'rov — `401`.

Istisnolar: `GET /health` (monitoring uchun ochiq) va
`POST /api/v1/auth/stream` (uni MediaMTX o'zi chaqiradi, mijoz emas).

## Kamera qo'shish → id olish

```bash
curl -X POST http://SERVER:8010/api/v1/admin/cameras \
  -H "X-API-Key: KALIT" -H "Content-Type: application/json" -d '{
    "name": "Chorsu 1", "region": "Toshkent",
    "external_id": "cam-toshkent-014",
    "ip": "192.168.1.64", "port": 554,
    "username": "admin", "password": "kamera-paroli",
    "vendor": "hikvision", "rtsp_path": "/Streaming/Channels/101"
  }'
# -> 201 {"id": 12, "external_id": "cam-toshkent-014", "codec": "H265",
#         "sub_path": "/Streaming/Channels/102", "sub_codec": "H264", ...}
```

`external_id` — sizning tizimingizdagi ID. Keyin hamma joyda `12`
o'rniga `ext:cam-toshkent-014` deb murojaat qilasiz — mapping jadval
kerak emas. Takror `external_id` → `409`. Takror kamera (bir xil
IP+port+RTSP yo'l) ham → `409` — bexosdan ikki nusxa yaratilmaydi.

Yaratish javobida holat (`state`) darhol tekshirilgan bo'ladi
("unknown" kutish yo'q); `model`/`firmware` fonda ONVIF/ISAPI'dan
so'ralib bir necha soniyada bazaga yoziladi — keyingi so'rovda ko'rinadi.

Yoqish/o'chirib qo'yish uchun to'liq PUT shart emas:

```
POST /api/v1/admin/cameras/{ref}/enabled   {"enabled": false}
```

O'chirilgan kameraga oqim ham, surat ham berilmaydi.

## Qurilmani avtomatik aniqlash (skaner)

RTSP yo'lini ham, kanal raqamlarini ham bilish shart emas: **IP va
login/parol yetadi**. Skaner ishlab chiqaruvchi shablonini o'zi topadi,
kanallarni 8 talik bloklarda tekshiradi va bo'sh blok kelganda to'xtaydi.

Diagnostika konsolidagi "Qurilma qo'shish" sahifasi aynan shu chaqiruvlardan
iborat — yopiq yo'l yo'q, hammasini o'zingiz ham qura olasiz.

**1) Skanni boshlash** — darhol qaytadi, natijalar SSE'da:

```bash
curl -X POST http://SERVER:8010/api/v1/devices/scan \
  -H "X-API-Key: KALIT" -H "Content-Type: application/json" \
  -d '{"ip": "192.168.170.160", "username": "admin", "password": "...",
       "max_channels": 64}'
# -> 202 {"job_id": "a1b2c3", "events": "/api/v1/devices/scan/a1b2c3/events"}
```

**2) Natijalar kanal sari** — birinchi kanal bir-ikki soniyada keladi,
oxirini kutish shart emas:

```
curl -N -H "X-API-Key: KALIT" \
  http://SERVER:8010/api/v1/devices/scan/a1b2c3/events

event: meta     {"vendor": "hikvision", "vendor_name": "Hikvision"}
event: channel  {"channel": 1, "ok": true, "codec": "H265",
                 "resolution": "1920x1080", "needs_transcode": true,
                 "rtsp_path": "/Streaming/Channels/101",
                 "snapshot_url": "/api/v1/devices/scan/a1b2c3/snapshot/1"}
event: channel  {"channel": 10, "ok": false, ...}      # bo'sh slot
event: done     {"device": "nvr", "live_channels": 9, "vendor": "hikvision"}
```

Qurilma javob bermasa `event: error` keladi va sababi yoziladi
(`tarmoq` / `parol` / `oqim` / `rtsp` bosqichlaridan eng ma'nolisi):

```
event: error    {"message": "10.0.0.9:554 javob bermadi — kamera o'chiq
                 yoki boshqa tarmoqda"}
```

Job xotirada 10 daqiqa yashaydi. Kech ulangan mijoz ham hammasini
boshidan oladi — hodisalar job ichida saqlanadi.

**3) Kanal surati** — hali saqlanmagan qurilmadan kadr. Foydalanuvchi
"qaysi kanal kerak" degan savolga rasmga qarab javob beradi:

```
GET /api/v1/devices/scan/a1b2c3/snapshot/1     -> image/jpeg
```

> Bu manzilni faqat `ok: true` kanallar uchun so'rang. O'lik kanalda u
> HTTP-snapshot manzillarini birma-bir sinab, keyin RTSP'dan kadr olishga
> urinadi — javob 30 soniyagacha cho'zilishi mumkin. `channel` hodisasi
> `snapshot_url` ni aynan shu sababdan faqat tirik kanalda to'ldiradi.

**4) Qurilma pasporti** — model, firmware, seriya (ONVIF, zaxira ISAPI).
Skan bilan **parallel** so'ralsa sahifa tezroq to'ladi:

```
GET /api/v1/devices/info?ip=192.168.170.160&username=admin&password=...
-> {"manufacturer": "Hikvision", "model": "DS-7616NI-Q1",
    "firmware": "V4.83.015", "serial": "DS-7616NI-Q1...", "mac": "..."}
```

Saqlangan kamera uchun `ip` o'rniga `ref` bering
(`?ref=ext:cam-toshkent-014`) — u holda topilgan model/firmware bazaga
ham yozib qo'yiladi.

**5) Tanlanganlarni saqlash.** Ikki yo'l bor:

| Yo'l | Qachon |
|---|---|
| `POST /api/v1/admin/cameras` har kanal uchun | foydalanuvchi kanallarni **tanlab** oladi (konsol shunday qiladi) |
| `POST /api/v1/admin/nvr/import` | **hammasini** birdaniga; parallel tekshiradi va faqat javob berganlarini saqlaydi. Avval `"dry_run": true` bilan ko'ring |

Skan natijasidagi `rtsp_path` va `vendor` ni to'g'ridan `admin/cameras`
tanasiga qo'ying — qayta tekshirish kerak emas.

## Oqim olish — bitta kamera

```bash
curl -H "X-API-Key: KALIT" \
  "http://SERVER:8010/api/v1/cameras/ext:cam-toshkent-014/stream?quality=sub"
# -> {"webrtc_url": "http://SERVER:8889/..._sub/whep?token=...",
#     "stream_url": "http://SERVER:8888/..._sub/index.m3u8?token=...",
#     "mode": "sub"}
```

- `quality=sub` — past sifatli oqim (video devor setkasi uchun; ~0,5
  Mbit/s). Fokus/katta oynada parametrisiz chaqiring — asosiy oqim.
- `hevc=1` — brauzeringiz H.265 ni o'zi o'qiy olsa (Safari, ba'zi
  Chrome'lar) o'girish o'tkazib yuboriladi.
- Manzillardagi `token` — 1 soatlik chipta, oqim shu chiptasiz
  ochilmaydi. Har ko'rsatishdan oldin yangi so'rov qiling.
- Frontend tavsiyasi: avval `webrtc_url` (WHEP, eng tez), ulanmasa
  `stream_url` (HLS) — hls.js bilan.

## Oqim olish — devor (batch)

64 katak uchun 64 ta HTTP o'rniga bitta so'rov:

```bash
curl -X POST http://SERVER:8010/api/v1/streams \
  -H "X-API-Key: KALIT" -H "Content-Type: application/json" \
  -d '{"ids": [12, "ext:cam-toshkent-014", 45], "quality": "sub"}'
```

```json
{
  "streams": {
    "12":  {"webrtc": "...", "hls": "...", "poster": "/api/v1/cameras/12/snapshot", "mode": "sub"},
    "ext:cam-toshkent-014": {"webrtc": "...", "hls": "...", "poster": "...", "mode": "sub"},
    "45":  {"error": "offline"}
  },
  "egress_estimate_mbps": 32.0
}
```

Har id mustaqil: topilmagani/o'chirilgani `error` bilan qaytadi,
qolganlari ishlayveradi. Chegara: 128 id. So'ralgan sub yo'llar 10
daqiqa "issiq" tutiladi — qayta ochilish < 1 soniya.

`poster` manzilini `<video poster=...>` ga qo'ying — katak bir zumda
surat bilan ochiladi, video orqada ulanadi.

## Holat: boshlang'ich + jonli (SSE)

Poll qilmang. Ulanishda bir marta to'liq rasm:

```bash
curl -H "X-API-Key: KALIT" \
  "http://SERVER:8010/api/v1/cameras/status?all=1"
# yoki ?ids=12,ext:cam-toshkent-014
# -> har kamera: {id, external_id, state, codec, sub_codec, resolution,
#                 last_seen, snapshot_at}
```

Keyin faqat o'zgarishlar SSE bilan keladi:

```bash
curl -N -H "X-API-Key: KALIT" http://SERVER:8010/api/v1/events
# event: state
# data: {"id": 45, "external_id": "cam-...", "state": "offline", "at": "..."}
# event: snapshot
# data: {"id": 45, "external_id": "cam-...", "at": "..."}
```

Holatlar: `online / offline` (TCP tekshiruv, ~60 s ichida), `stalled`
(oqim muzladi — bayt kelmayapti, ~30 s ichida; tiklansa `online`).
Har 15 s `: keepalive` komment keladi — ulanish tirikligining belgisi.
Chegara: 50 abonent. Uzilsa qayta ulaning va avval `status` ni oling.

JavaScript misoli:

```js
const es = new EventSource("/nigoh/api/v1/events");   // proksi orqali
es.addEventListener("state", (e) => {
  const d = JSON.parse(e.data);      // {id, external_id, state, at}
  kameraHolatiniYangila(d.external_id, d.state);
});
```

(EventSource sarlavha qo'ya olmaydi — SSE'ni o'z backend'ingiz orqali
proksilang yoki shu proksida X-API-Key qo'shing.)

## Ish vaqti tarixi (uptime)

Har kamera qachon uzilgan/qaytgani bazada yuritiladi (30 kun).
Statistika tayyor hisoblangan holda keladi:

```bash
curl -H "X-API-Key: KALIT" \
  "http://SERVER:8010/api/v1/admin/cameras/ext:cam-toshkent-014/uptime?hours=168"
# -> {"hours": 168, "uptime_pct": 99.4, "offline_seconds": 3620,
#     "outages": 3, "last_offline_at": "2026-08-19 22:14:03",
#     "segments": [{"state": "online", "from": "...", "to": "...",
#                   "seconds": 86400}, ...],
#     "transitions": [{"ts": "...", "kind": "offline"}, ...]}
```

`hours` — davr (standart 168 = 7 kun, ko'pi 720). `segments` dan
vaqt chizig'i chiziladi, `transitions` — xom o'tishlar ro'yxati.

## Surat (snapshot)

```
GET /api/v1/cameras/{ref}/snapshot     -> image/jpeg, ETag bilan
```

Suratlar serverda diskda turadi va o'zi yangilanadi (so'ralgan kamera
tez-tez, qolgan onlaynlar kamera soniga moslashgan oraliqda).
`If-None-Match` yuborsangiz o'zgarmagan surat `304` qaytadi.
Yangilanish payti SSE `snapshot` hodisasida.

Muhim semantika:

- **Kamera `offline`/`disabled` bo'lsa — `404`.** Bir hafta oldingi
  kadr "jonli" bo'lib ko'rinmasin. `<img>`/`poster` 404 da o'zi qora
  bo'ladi — qo'shimcha kod kerak emas.
- Surat holat "online" desa ham haddan eski bo'lsa — `404` (kamera
  kadr bermay qo'ygan holat).
- **`?stale=1`** — oxirgi ma'lum kadr baribir beriladi ("oxirgi kadrni
  ko'rish" tugmasi uchun; kalit baribir shart).
- Javobda `X-Snapshot-At` (UTC) va `X-Snapshot-Age` (soniya) bor —
  "3 daq oldin" yozuvi yoki xiralashtirish kabi qarorlarni shu asosda
  o'zingiz qiling; servis faqat ko'rsat/ko'rsatma'ni hal qiladi.

**Va eng muhimi:** surat so'rovini kutmang — SSE'da `state: offline`
kelgan zahoti o'sha katakning `poster`ini tozalang va OFFLINE belgisini
qo'ying. Endpoint'dagi `404` — ikkinchi himoya qatlami, birinchi yechim
SSE'da.

## Salomatlik va sig'im

```
GET /health   (kalitsiz)
-> {"ok": true, "mediamtx": true, "egress_mbps": 84.0,
    "egress_capacity_mbps": 1000, "streams": 18, "readers": 4,
    "warm": 12, "managed": 9, "sse_subscribers": 3,
    "health": {...}, "open_ms": {...}}
```

`egress_mbps` — serverdan chiqayotgan video trafigi. Ko'payish nuqtasi
serverda (100 tomoshabin = 100 chiqish oqimi), shuning uchun bu raqamni
o'z monitoringingizga ulang: 80% — ogohlantirish, 95% — jiddiy.
`NIC_CAPACITY_MBPS` muhit o'zgaruvchisi bilan sozlanadi.

`managed` — ayni damda MediaMTX'da ro'yxatda turgan yo'llar. **Kameralar
soniga emas, tomoshabinlar soniga qarab o'sishi kerak**: yo'llar talab
bo'yicha yaratiladi (sabab va o'lchovlar — `docs/BENCHMARK.md`). Bu son
kamera soniga yaqinlashib qolsa, kimdir hamma kamerani `always_on`
qilgan degani.

## Uzilishlar tahlili

5000 kamerani ko'z bilan kuzatib bo'lmaydi. Ikkita savolga javob bor,
ikkalasi ham `events` jadvalidagi online/offline o'tishlaridan (30 kun).

**Kim aybdor** — guruh reytingi, eng yomoni birinchi:

```
GET /api/v1/admin/uptime?hours=24&group_by=nvr
-> {"groups": [{"key": "10.0.54.10", "cameras": 64, "online": 61,
                "offline": 3, "outages": 240, "offline_seconds": 103680,
                "uptime_pct": 95.5}, ...]}
```

`group_by`: `region` (hududsiz kameralar `belgilanmagan` guruhiga
tushadi), `nvr` (registrator manzili — bitta IP ortida o'nlab kanal),
`node` (MediaMTX tuguni). `group_by` bermasangiz kamera kesimida
qaytadi (`limit`, standart 100, uzilishlar bo'yicha kamayish tartibida).

**Qachon buzilyapti** — sutka bo'ylab taqsimot:

```
GET /api/v1/admin/outages/hourly?hours=24&tz_offset_minutes=300
-> {"hourly": [12, 8, ...24 ta...], "total": 14844,
    "peak": {"from_hour": 23, "to_hour": 2, "outages": 1916}}
```

`tz_offset_minutes` — mijozning zonasi
(`-new Date().getTimezoneOffset()`): hodisalar bazada UTC'da yotadi,
lekin "cho'qqi 08:00 da" degan xulosa faqat mahalliy vaqtda ma'noga
ega. `peak` — eng zich uch soatlik oyna (sutka aylanasidan o'tadi:
23:00–02:00 ham topiladi); texnik xizmatni shu oynadan tashqariga
rejalashtiring. `ref` berilsa bitta kamera bo'yicha.

O'lchovlar (5000 kamera): `docs/BENCHMARK.md`.

## Ochilish vaqtini o'lchash

"Sekin ochilyapti" degan shikoyatga bitta raqam bilan javob bo'lmaydi —
ochilish uch bo'lakdan iborat va har biri boshqa joyni ko'rsatadi.
Pleyeringiz ularni o'lchab yuborsa, `/health` ularni p50/p95 qilib
qaytaradi:

```
POST /api/v1/metrics/open        -> 204
{"camera_id": 12, "transport": "webrtc", "mode": "direct",
 "stream_ms": 24, "signal_ms": 180, "frame_ms": 640, "total_ms": 844}
```

| Maydon | Qayerdan qayergacha | Sekin bo'lsa nima qilish |
|---|---|---|
| `stream_ms` | bosildi → `/stream` javobi | MediaMTX'da ortiqcha yo'llar (`pending_paths`) yoki uzoq tugun API'si |
| `signal_ms` | `/stream` javobi → WHEP javobi | tarmoq/proksi. HLS'da bu bosqich yo'q (0) |
| `frame_ms` | WHEP javobi → birinchi kadr | kameradagi `I Frame Interval` — registratorda sozlanadi |
| `total_ms` | foydalanuvchi ko'rgan to'liq vaqt | |

`transport`: `webrtc`, `hls` yoki `hls_fallback` (WebRTC urinib
ko'rilib, yiqilgandan keyingi HLS — uning kutishiga muvaffaqiyatsiz
urinish ham qo'shilgan, shuning uchun toza HLS bilan aralashtirilmaydi).

```
GET /health -> "open_ms": {
    "webrtc": {"n": 128, "total_ms": {"p50": 840, "p95": 2100},
               "frame_ms": {"p50": 610, "p95": 1900}, ...}}
```

Namunalar xotirada (oxirgi 512 ta har transport uchun) — server qayta
ishga tushsa hisob noldan boshlanadi.

## Yakuniy API yuzasi

| Metod | Yo'l | Izoh |
|---|---|---|
| POST | `/api/v1/devices/scan` | `202` + job, natijalar SSE |
| GET | `/api/v1/devices/scan/{job}/events` | SSE — kanallar kelgan sari |
| GET | `/api/v1/devices/scan/{job}/snapshot/{ch}` | topilgan kanal surati |
| GET | `/api/v1/devices/info` | model, firmware, seriya |
| POST | `/api/v1/admin/cameras` | saqlash → `id`; takror IP+yo'l → `409` |
| PUT / DELETE | `/api/v1/admin/cameras/{ref}` | `ref` = id yoki `ext:...` |
| POST | `/api/v1/admin/cameras/{ref}/enabled` | yoqish/o'chirib qo'yish |
| GET | `/api/v1/admin/cameras/{ref}/uptime` | uptime %, uzilishlar, segmentlar |
| GET | `/api/v1/cameras` | yengil ro'yxat |
| GET | `/api/v1/cameras/status` | `?ids=` yoki `?all=1` |
| GET | `/api/v1/cameras/{ref}/stream` | bitta oqim (chiptali) |
| GET | `/api/v1/cameras/{ref}/snapshot` | JPEG, ETag |
| **POST** | **`/api/v1/streams`** | **batch chipta, 128 tagacha** |
| GET | `/api/v1/events` | SSE — holat o'zgarishlari |
| POST | `/api/v1/metrics/open` | pleyer o'lchagan ochilish vaqti → `204` |
| GET | `/api/v1/admin/nodes` | MediaMTX tugunlari |
| GET | `/api/v1/admin/uptime` | uzilishlar reytingi (`group_by=region\|nvr\|node`) |
| GET | `/api/v1/admin/outages/hourly` | sutkalik profil + cho'qqi oyna |
| GET | `/health` | servis, MediaMTX, egress (kalitsiz) |
| POST | `/api/v1/auth/stream` | MediaMTX chaqiradi, mijoz emas |

## Pleyer qurishdagi saboqlar (o'zimiz bosgan tuzoqlar)

O'z saytingizda pleyer yozsangiz, quyidagilar ko'p vaqt tejaydi —
barchasi haqiqiy ishga tushirishda uchragan muammolar:

1. **WebRTC → HLS zaxira tartibida `video.srcObject`ni tozalang.**
   WebRTC yiqilib HLS'ga o'tganda o'lik `srcObject` elementda qolsa,
   u `src`dan ustun bo'lgani uchun HLS hech qachon ko'rinmaydi —
   xatosiz qora ekran. HLS'dan oldin: `video.srcObject = null`.
2. **WebRTC yiqilishini eslab qoling** (masalan, localStorage'da,
   10 daqiqa amal qilsin) — UDP yopiq muhitda har ochilishda 3-5 s
   bekor kutilmaydi; port ochilsa tez yo'lga o'zi qaytadi.
3. **hls.js'da `enableWorker: false`** — sahifangizda qattiq CSP
   bo'lsa worker (blob/eval) bloklanib pleyer jim qotadi.
4. **Oldindan isiting**: kamera sahifasi ochilganda playlist'ni bir
   marta `fetch` qilib qo'ying — MediaMTX kameraga ulanib segment
   yig'ishni boshlaydi, play bosilganda 2-3 s da ochiladi.
5. **Barqarorlik uchun**: `liveSyncDurationCount: 2`, bufer ~12 s,
   `maxLiveSyncPlaybackRate: 1.1` — titroq tarmoqda qotmaydi.
6. Nigoh oldida qo'shimcha proxy bo'lsa: MediaMTX redirect'lari va
   cookie yo'llari prefiksga qayta yozilishi shart — tayyor namuna
   `deploy/negoh.das-uty.uz.conf` da (`proxy_redirect`,
   `proxy_cookie_path`).

## Video sahifangizda HTTPS

Sahifangiz HTTPS bo'lsa, brauzer `http://...:8888` videoni bloklaydi.
Yechim: Nigoh serverida nginx + `.env` da
`MEDIA_BASE=https://domen/media` — oqim manzillari bitta HTTPS domen
ostidan keladi. Namuna: `docs/DEPLOY.md`, HTTPS bo'limi.

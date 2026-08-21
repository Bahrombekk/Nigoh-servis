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

Qurilmani qo'lda bilmasangiz — skaner topib beradi:

```bash
# 202 + job_id qaytadi, natijalar SSE bilan kanal sari keladi
curl -X POST http://SERVER:8010/api/v1/devices/scan \
  -H "X-API-Key: KALIT" -H "Content-Type: application/json" \
  -d '{"ip": "192.168.1.100", "username": "admin", "password": "..."}'
# -> {"job_id": "a1b2c3", "events": "/api/v1/devices/scan/a1b2c3/events"}

curl -N -H "X-API-Key: KALIT" \
  http://SERVER:8010/api/v1/devices/scan/a1b2c3/events
# event: meta     -> {"vendor": "hikvision", ...}
# event: channel  -> {"channel": 1, "ok": true, "codec": "H265",
#                     "snapshot_url": ".../snapshot/1", ...}
# event: done     -> {"device": "nvr", "live_channels": 11, ...}
```

Qurilma pasporti (model, firmware, seriya):
`GET /api/v1/devices/info?ref=ext:cam-toshkent-014`

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

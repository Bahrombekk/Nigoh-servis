# Nigoh — frontendchi uchun qo'llanma

> Bu hujjat **video ko'rsatish** haqida: ro'yxatni chizish va oqimni
> `<video>` elementiga ulash. Kamera qo'shish, holat kuzatish va
> uzilishlar tahlili — [INTEGRATION.md](INTEGRATION.md) da.

Bu hujjat kameralar bilan **hech qachon ishlamagan** frontendchi uchun
yozilgan. Yaxshi yangilik: kamera protokollarini (RTSP va h.k.) umuman
bilishingiz shart emas — servis hammasini oddiy HTTP API ga aylantirib
beradi. Sizning ishingiz: ro'yxatni chizish, video elementga ulash.

Interaktiv API hujjati serverning o'zida: **`http://SERVER:8010/docs`**.
Ishlayotgan namuna ham bor: `debug-ui/app.js` — pleyer, video devor va
diagnostika shu API bilan qurilgan, undan nusxa ko'chirish mumkin
(`createPlayer` funksiyasiga qarang: watchdog, HLS tiklash va qayta
ulanish tayyor holda).

## Lug'at (2 daqiqa)

| Atama | Ma'nosi |
|---|---|
| **RTSP** | Kameraning o'z protokoli. Siz unga hech qachon tegmaysiz — servis ichida qoladi. |
| **HLS** | Video HTTP orqali (`.m3u8` manzil). Hamma brauzerda ishlaydi, ochilishi 1–2 s. |
| **WebRTC** | Jonli video uchun eng tez yo'l (~0,5 s). Servis WHEP standartida beradi. |
| **Chipta (token)** | Oqim manziliga qo'shilgan `?token=...` — usiz video ochilmaydi. Siz uni yaratmaysiz, API tayyor qo'shib beradi. |
| **Sub-oqim** | Kameraning past sifatli 2-varianti — setkada ko'p video ko'rsatganda ishlatiladi. |

## Eng muhim qoida

**Oqim manzilini saqlamang va qattiq kodlamang.** Har safar foydalanuvchi
kamerani ochmoqchi bo'lganda `GET /api/v1/cameras/{id}/stream` ni chaqiring —
javobdagi manzil tayyor chipta bilan keladi (chipta 1 soat yashaydi).

## Asosiy oqim: ro'yxatdan videogacha

### 1. Kameralar ro'yxati

```
GET /api/v1/cameras
GET /api/v1/cameras?bbox=39.5,64.0,42.0,72.0   (xarita to'rtburchagi bilan)
```

```json
{
  "total": 17, "shown": 17,
  "cameras": [{
    "id": 1, "name": "Amir Temur xiyoboni", "region": "Toshkent",
    "lat": 41.3111, "lng": 69.2797,
    "state": "online",
    "codec": "H264", "resolution": "1920x1080",
    "last_seen": "2026-08-18T05:00:00+00:00"
  }]
}
```

`state` — kameraning yagona holati, UI rangini shunga qarab tanlang:

| state | Ma'nosi | Tavsiya |
|---|---|---|
| `online` | ishlayapti | yashil |
| `offline` | tarmoqdan javob yo'q | qizil |
| `stalled` | ulangan-u, tasvir kelmayapti | sariq |
| `unknown` | hali tekshirilmagan | kulrang |
| `disabled` | admin o'chirgan | ro'yxatda chiqmaydi |

Ro'yxatni 15 soniyada bir yangilash yetarli — tez-tez so'ramang.

### 2. Avval surat (poster), keyin video

Video ulanguncha 1–2 soniya o'tadi. Foydalanuvchiga "bir zumda ochildi"
tuyulishi uchun avval kamera suratini ko'rsating:

```html
<video id="player" autoplay muted playsinline
       poster="/api/v1/cameras/1/snapshot"></video>
```

### 3. Oqim manzilini olish

```
GET /api/v1/cameras/1/stream
```

```json
{
  "webrtc_url": "http://SERVER:8889/toshkent_amir_temur/whep?token=...",
  "stream_url": "http://SERVER:8888/toshkent_amir_temur/index.m3u8?token=...",
  "mode": "direct"
}
```

E'tibor bering: video **8010-portdan emas**, 8888/8889-portlardan keladi —
bu normal, manzillar tayyor holda beriladi, shunchaki ishlating.

Ixtiyoriy parametrlar:
- `?quality=sub` — past sifatli oqim (4×4 setka uchun; kamerada sub
  bo'lmasa asosiysi qaytadi).
- `?hevc=1` — brauzer H.265 ni o'qiy olsa qo'shing (pastda misol).

### 4. Videoni ulash: avval WebRTC, bo'lmasa HLS

To'liq, nusxalab ishlatiladigan player:

```html
<script src="https://cdn.jsdelivr.net/npm/hls.js@1"></script>
<script>
// WebRTC (WHEP) — eng tez yo'l
async function playWebRTC(video, whepUrl) {
  const pc = new RTCPeerConnection();
  pc.addTransceiver("video", { direction: "recvonly" });
  pc.ontrack = (e) => { video.srcObject = e.streams[0]; };
  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  const res = await fetch(whepUrl, {
    method: "POST",
    headers: { "Content-Type": "application/sdp" },
    body: offer.sdp,
  });
  if (!res.ok) { pc.close(); throw new Error("WHEP " + res.status); }
  await pc.setRemoteDescription({ type: "answer", sdp: await res.text() });
  return pc;   // yopish uchun: pc.close()
}

// HLS — zaxira yo'l
function playHLS(video, m3u8Url) {
  if (video.canPlayType("application/vnd.apple.mpegurl")) {
    video.src = m3u8Url;                 // Safari o'zi o'qiydi
  } else {
    const hls = new Hls({ lowLatencyMode: true });
    hls.loadSource(m3u8Url);
    hls.attachMedia(video);
    return hls;                          // yopish uchun: hls.destroy()
  }
}

// Brauzer H.265 ni o'qiy oladimi (Chrome/Edge/Safari — ha, Firefox — yo'q)
const hevcOk = window.MediaSource &&
  MediaSource.isTypeSupported('video/mp4; codecs="hvc1.1.6.L123.B0"');

async function openCamera(video, cameraId) {
  video.poster = `/api/v1/cameras/${cameraId}/snapshot`;   // darhol surat
  const r = await fetch(
    `/api/v1/cameras/${cameraId}/stream?hevc=${hevcOk ? 1 : 0}`);
  if (r.status === 403) { alert("Bu kamerani ko'rishga ruxsat yo'q"); return; }
  const s = await r.json();
  try {
    if (s.webrtc_url) return await playWebRTC(video, s.webrtc_url);
    throw new Error("webrtc yo'q");
  } catch {
    return playHLS(video, s.stream_url);   // WebRTC o'tmadi — HLS
  }
}
</script>
```

**Muhim:** foydalanuvchi boshqa kameraga o'tganda eski ulanishni yoping
(`pc.close()` / `hls.destroy()`) — aks holda ko'rinmas video tarmoq va
protsessorni yeyveradi.

## Kirish

Nigoh'da **rollar yo'q** — ular sizning tizimingizda. Servis bitta
savolga javob beradi: so'rov ishonchli manbadanmi.

Shuning uchun brauzer Nigoh'ga **to'g'ridan murojaat qilmasligi kerak**:
`NIGOH_API_KEY` server kaliti, uni frontend kodiga qo'ysangiz uni
har kim ko'radi. To'g'ri sxema:

```
brauzer  →  sizning backend  →  Nigoh (X-API-Key)
           (rollarni shu yerda tekshirasiz)
```

Sizning backend'ingiz `/api/v1/cameras/{id}/stream` ni chaqiradi va
javobdagi manzilni brauzerga uzatadi. Manzildagi chipta 1 soat yashaydi
va faqat o'sha bitta yo'lga tegishli.

`/api/v1/auth/login` va `/auth/me` ham bor, lekin ular faqat
**diagnostika konsoli** uchun (`ENABLE_UI=1`) — sizning ilovangizga
kerak emas.

## Uzilishlar tahlili

Kamera sahifasida "bu kamera qanchalik ishonchli" degan savolga javob:

```
GET /api/v1/admin/cameras/{ref}/history?days=30&tz_offset_minutes=300
```

Bitta so'rovda: mavjudlik foizi, uzilishlar soni, MTTR/MTBF, soatlik
profil, 30 kunlik kalendar va uzilishlar jurnali. Park bo'yicha
reyting — `/api/v1/admin/uptime?group_by=region`. To'liq tavsif:
[INTEGRATION.md](INTEGRATION.md).

## Ko'p uchraydigan xatolar

| Muammo | Sababi |
|---|---|
| Video qora, xato yo'q | `muted` va `playsinline` atributlari qo'yilmagan — brauzer avto-ijroni bloklaydi |
| 401 oqim so'rovida | Chipta eskirgan — `/stream` ni qayta chaqiring (saqlab qo'ygansiz, mumkin emas edi) |
| 401 hamma so'rovda | `X-API-Key` yuborilmagan yoki noto'g'ri |
| 404 `/snapshot` da | Kamera offline — eski kadr jonli bo'lib ko'rinmasin deb berilmaydi (`?stale=1` bilan olinadi) |
| WebRTC ulanmayapti | 8889 (tcp) yoki 8189 (**udp va tcp**) yopiq — HLS zaxirasi baribir ishlashi kerak |
| Firefox'da H.265 kamera sekin ochiladi | Normal: server uni H.264 ga o'girib beradi, bu 1–2 s qo'shadi |
| Video ochildi-yu keyin qotdi | `connectionState` buni ko'rsatmaydi — `getStats().framesDecoded` ni sanang (namuna: `debug-ui/app.js`) |

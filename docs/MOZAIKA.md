# Devor Kompozit (server mozaikasi) — holat va topilmalar

Sana: 2026-09-09. Branch: `mozaika-devor`.

Maqsad: 100+ kamerani brauzerni muzlatmasdan jonli ko'rsatish. Yechim
g'oyasi — brauzer 36 ta oqim emas, serverda bitta rasmga birlashtirilgan
BITTA oqim ochsin (dekod yuki brauzerdan serverga o'tadi).

## Arxitektura (relay o'rtada)

    Kamera(sub) → MediaMTX `<slug>_sub` relay (orqa xonda issiq)
                → FFmpeg xstack (mozaika, NVENC) → bitta oqim → Brauzer

Mozaika kameradan TO'G'RIDAN o'qimaydi — MediaMTX ushlab turgan lokal
relaydan o'qiydi. Bitta kamera = bitta ulanish, necha devor bo'lsa ham.

Kod:
- `media/mosaic.py` — FFmpeg xstack buyrug'i (setpts=N/(fps*TB) — jonli
  oqimlar PTS'ini tenglaydi; `-timeout` (ffmpeg 8.1, eski `-rw_timeout` yo'q)).
- `media/walls.py` — `wall_relays(key)`: har katak uchun lokal relay nomi
  (`<slug>_sub`); kodegi bo'sh kamera → None (qora katak).
- `media/launcher.py` — `run_wall`: `_ready_set()` (MediaMTX API'dan tayyor
  relaylar) + `_warm()` (sovuqlarini isitadi); faqat kadr berayotgan relay
  real katak bo'ladi.
- `api/walls.py` — `POST /walls`: tanlovni saqlaydi, bitta oqim manzili +
  katak xaritasi (`tiles`) qaytaradi. Kalit = sha1(kamera_id'lar+setka) —
  bir xil tanlovni ko'p operator so'rasa, bitta oqimni bo'lishadi.
- Frontend `debug-ui/app.js` — Devor → Kompozit rejimi: bitta `<video>`,
  bitta oqim; katakni bosish → o'sha kamera diagnostikasi.

## Ishlaydi

- Relay o'rtada arxitekturasi: `<slug>_sub` relaylar issiq turadi (sinovда
  33 tadan 31 tasi ready).
- `POST /walls` → 200, tiles xaritasi to'g'ri; bosish→katak→kamera moslashadi.
- Kichik setka: **2×2 (3 real relay)** publish bo'ladi.
- Publish + HLS o'qish token bilan → 200 (1280×720 mozaika, avc1, H.264).

## Ishlamaydi / cheklov (haqiqiy kameralarda o'lchandi)

| Setka | Natija |
|-------|--------|
| 2×2 (3 real) | publish, lekin **~22 s** (sekin) |
| 3×3 (8 real) | **publish bo'lmadi** (cascade / korruptsiya) |

### Ildiz sabablar

1. **Bitta FFmpeg N kirishni KETMA-KET ochadi.** Har kirish `find_stream_info`
   da navbatdagi keyframe'ni kutadi (~7 s/kirish, chunki sub GOP uzun —
   keyframe har 3-4 s). 3 kirish = 22 s; 8 kirish = 60 s+ → oxirgi relaylar
   sovib yopiladi (`Failed reading RTSP data: End of file`) → mozaika hech
   qachon yig'ilmaydi.
2. **"Sub" oqim aslida past sifat EMAS.** Ko'p kamerada subtype=1 ham
   1920×1080 / 1280×960. 8-9 tasini birdan dekod qilish mashinani to'ldiradi
   → `corrupt decoded frame`.

### Sinalgan, YETARLI EMAS

- `analyzeduration/probesize` (1M…5M, 0) — 5M "unspecified size" ni tuzatdi,
  lekin ochilish sekinligini yechmaydi.
- `-timeout` (ffmpeg 8.1 mosligi) — kerak edi, lekin yetarli emas.
- `setpts=N/(fps*TB)` — PTS tenglash, xstack qotishini kamaytiradi, lekin
  ketma-ket ochilishni tezlashtirmaydi.
- Parallel "keeper" (relaylarni issiq ushlash) — cascade'ni kamaytirdi,
  lekin baribir publish bo'lmadi (korruptsiya + sekinlik).
- Keyframe-repeater (ONVIF) — 4.6s → 2.9s, rate-limiter cheklaydi, kam foyda.
- SDP'da `sprop-parameter-sets` bor (o'lcham SDP'da) — minimal-probe biroz
  tezlashtirdi, lekin publish baribir bo'lmadi.

## Keyingi yo'llar (kelajakda)

1. **Parallel sub-mozaikalar.** Setkani kichik bloklarga bo'lib (≤4 kirish),
   har blokni ALOHIDA FFmpeg parallel qursin, so'ng yakuniy FFmpeg bu
   server-generatsiya (qisqa GOP, tez ochiladigan) bloklarni birlashtirsin.
   Ketma-ketlikni parallellashtirib ochilishni ~15 s ga tushiradi.
2. **Har kamerani normallashtirish.** Har kamera sub'ini ALOHIDA FFmpeg
   past sifat + qisqa GOP + doim-issiq oraliq oqimga o'girsin; mozaika
   shundan o'qisin (tez ochiladi, yengil dekod). Lekin bu har kameraga
   dekod+kodlash qo'shadi → GeForce NVENC 8 sessiya chegarasiga uriladi →
   datacenter GPU yoki CPU kodlash kerak.

## Asosiy tushuncha

Mozaika BRAUZER yukini yechadi (1 dekod), lekin SERVER yukini yechmaydi:
36 kamerani jonli ko'rsatish uchun server baribir 36 tani dekod + 1 kodlash
qiladi. Bu — masshtabning haqiqiy narxi. Bu mashinada (GeForce, 8 NVENC
sessiya) katta devor og'ir; kichik setka (2×2, 3×3) real, lekin tezlashtirish
kerak.

Foydalanuvchi ehtiyoji: doimiy 36-kamera kuzatuv EMAS — talab bo'yicha,
asosan muammoli kameralar. Shu sababli kichik setka + Jonli rejimi
ko'p holatga yetarli bo'lishi mumkin.

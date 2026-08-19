# Nigoh — tizimni 0 dan tushunish

Bu hujjat loyiha egasi uchun: hech qanday tayyorgarliksiz o'qib, tizim
nima, qanday ishlaydi va nega aynan shunday qurilganini to'liq tushunish
uchun. Boshqa hujjatlar rol bo'yicha: [DEPLOY.md](DEPLOY.md) (serverga
qo'yish), [BACKEND.md](BACKEND.md), [FRONTEND.md](FRONTEND.md).

---

## 1. Nigoh nima?

**Bir jumlada:** IP kameralarni bitta joyga yig'ib, xaritada ko'rsatib,
jonli tasvirini brauzerda ochib beradigan servis.

**Qanday muammoni hal qiladi:** IP kamera tasvirni **RTSP** degan eski
protokolda beradi — brauzer uni tushunmaydi. Kameralar minglab bo'lishi,
har xil zavodniki (Hikvision, Dahua, Huawei...), har xil kodekda ishlashi
mumkin. Nigoh mana shu tartibsizlikni bitta oddiy HTTP API ga aylantiradi:
frontendchi "kamera №5 ning videosini ber" deydi, qolgan hamma murakkablik
servis ichida qoladi.

---

## 2. Asosiy tushunchalar (0 dan)

Bularni bilsangiz qolgan hammasi oson tushuniladi.

**IP kamera** — tarmoqqa ulangan kamera. O'z IP manzili, login/paroli bor.
Tasvirni so'ragan tomonga RTSP orqali uzatadi.

**NVR (registrator)** — bir nechta kamera ulanadigan quti. Tashqaridan
bitta IP ko'rinadi, kanallari raqam bilan so'raladi (1-kanal, 2-kanal...).
1000 ta kamera odatda 20–40 ta NVR ortida turadi.

**RTSP** — kameraning "ona tili". `rtsp://login:parol@192.168.1.10:554/yo'l`
ko'rinishida so'raladi. Brauzer buni **o'qiy olmaydi** — shuning uchun
o'rtada tarjimon kerak.

**Kodek** — videoni siqish usuli. Ikkitasi muhim:
- **H.264** — eski, hamma joyda ishlaydi;
- **H.265 (HEVC)** — yangiroq, 2 barobar tejamkor, lekin Firefox o'qimaydi.
  O'qimaydigan brauzer uchun H.264 ga **o'girish (transcode)** kerak — bu
  qimmat ish (protsessor/GPU yeydi), shuning uchun faqat ilojsiz qolganda
  qilinadi.

**HLS** — videoni oddiy HTTP fayllar (`.m3u8` + segmentlar) qilib berish.
Hamma brauzerda ishlaydi, kechikishi 1–2 soniya.

**WebRTC** — brauzerning jonli video texnologiyasi. Eng tez yo'l
(~0,5 soniya), lekin H.265 ni bilmaydi.

**MediaMTX** — tayyor ochiq-kodli media-server (bitta .exe fayl). Aynan u
RTSP'ni oladi va HLS/WebRTC qilib tarqatadi. Nigoh'ning "video dvijoki" shu.

**FFmpeg** — video bilan hamma narsani qiladigan universal vosita. Nigoh'da
faqat bitta ish uchun: H.265 → H.264 o'girish (kerak bo'lganda).

**Keyframe** — videoning "to'liq surat" kadri (qolgan kadrlar faqat farqni
saqlaydi). Ijro faqat keyframe'dan boshlanadi; kameralar uni 2–4 soniyada
bir yuboradi — ochilish tezligining asosiy chegarasi shu.

---

## 3. Katta rasm

Tizim ikki qatlamdan iborat — bu bo'linish butun loyihaning asosi:

```
                    FOYDALANUVCHI (brauzer)
                      │                │
        metadata (JSON)│                │video (HLS/WebRTC)
                      ▼                ▼
   ┌─────────── NIGOH BACKEND ─┐   ┌── MEDIAMTX ──────────┐
   │  BOSHQARUV QATLAMI (8010) │   │  MEDIA QATLAMI       │
   │  · kameralar bazasi       │──▶│  (8888 HLS,          │
   │  · login, rollar          │API│   8889 WebRTC)       │
   │  · monitoring, hodisalar  │   │                      │
   │  · REST API /api/v1       │   │  RTSP'ni o'zi tortadi│
   └───────────────────────────┘   └──────────┬───────────┘
                                              │ RTSP
                                       ┌──────┴──────┐
                                       │  KAMERALAR  │
                                       └─────────────┘
```

Uchta muhim qoida:

1. **Video backend orqali o'tmaydi.** Brauzer videoni to'g'ridan-to'g'ri
   MediaMTX'dan oladi. Backend faqat "manzil beruvchi" — shuning uchun
   1000 kamera bo'lsa ham backend yengil qoladi.
2. **Kamera faqat kimdir ko'rayotganda ulanadi** (on-demand). 1000 kamera,
   3 tomoshabin = 3 ta faol oqim. Qolgan 997 tasi hech narsa sarflamaydi.
3. **Baza — yagona haqiqat manbai.** MediaMTX'dagi holat har 30 soniyada
   baza bilan solishtirilib tuzatiladi (reconciler). MediaMTX yiqilsa ham,
   qayta ko'tarilib o'zini tiklaydi — qo'lda hech narsa qilinmaydi.

---

## 4. Bitta bosishda nima bo'ladi (qadam-baqadam)

Foydalanuvchi xaritada kamerani bosdi. Ichkarida shu ketma-ketlik yuradi:

```
1. Brauzer:  GET /api/v1/cameras/5/snapshot
   → kamera SURATI darhol ko'rinadi (~0,2 s) — "ochildi" hissi

2. Brauzer:  GET /api/v1/cameras/5/stream
   Backend shu payt:
   a) ruxsatni tekshiradi (operator bo'lsa — hududi to'g'rimi)
   b) MediaMTX'da bu kamera yo'li borligiga ishonch hosil qiladi
   c) kameraga "hozir keyframe yubor" buyrug'ini yuboradi (tezlik uchun)
   d) 1 soatlik imzoli CHIPTA yasab, manzillarga qo'shadi
   → javob: {"webrtc_url": "...?token=...", "stream_url": "...?token=..."}

3. Brauzer WebRTC manzilga ulanadi (ishlamasa — HLS)

4. MediaMTX chiptani backend'dan tekshirtiradi (401 bo'lsa video yo'q)

5. MediaMTX kameraga RTSP bilan ulanadi (agar hali ulanmagan bo'lsa)
   → video keladi. Tomoshabin ketgach 1 daqiqada kamera qo'yib yuboriladi.
```

Foydalanuvchi uchun bu "bosdim — ochildi". Servis uchun — 5 bosqichli,
har biri himoyalangan jarayon.

---

## 5. Papkalar: nima qayerda va nega

```
nigoh-servis/
├── main.py              kirish nuqtasi: bootstrap + uvicorn (54 qator xolos)
│
├── app/                 BOSHQARUV QATLAMI (web)
│   ├── config.py        sozlamalar — hammasi muhit o'zgaruvchisidan
│   ├── models.py        so'rov shakllari (Pydantic tekshiradi)
│   ├── helpers.py       "tarjima": baza qatori → brauzer/MediaMTX ko'rinishi
│   ├── bootstrap.py     birinchi ishga tushirish: baza, admin, fon xizmatlar
│   ├── routes_auth.py   /auth — kirish/chiqish + MediaMTX chipta tekshiruvi
│   ├── routes_public.py /cameras — xarita, oqim, surat (ochiq)
│   ├── routes_stats.py  /stats — dashboard tarixi
│   └── routes_admin.py  /admin — CRUD, NVR, skaner, foydalanuvchi, tugunlar
│
├── media/               MEDIA QATLAMI (MediaMTX bilan aloqa)
│   ├── sync.py          mediamtx.yml yaratish + jonli API (yo'l qo'shish/o'chirish)
│   ├── reconciler.py    har 30 s: holatni kelishtirish, yiqilsa qayta ko'tarish
│   └── launcher.py      H.265→H.264 o'girish jarayoni (FFmpeg)
│
├── core/                UMUMIY INFRATUZILMA (ikkala qatlam ishlatadi)
│   ├── db.py            SQLite sxema + migratsiya (ustun yetishmasa o'zi qo'shadi)
│   ├── security.py      parollar, sessiyalar, oqim chiptalari
│   ├── health.py        har 60 s: kameralar tirikmi (arzon TCP tekshiruv)
│   ├── rtsp_probe.py    kamerani chuqur tekshirish (tarmoq→parol→kodek→o'lcham)
│   ├── fast_start.py    surat (poster) + keyframe so'rash
│   ├── stats.py         dashboard tarixi (30 kun saqlanadi)
│   ├── events.py        hodisalar jurnali (muzladi/tiklandi/restart)
│   ├── alerts.py        Telegram ogohlantirishlari (ixtiyoriy)
│   └── log.py           strukturali JSON log (nigoh.log)
│
├── static/              test UI — xarita, video devor, boshqaruv (namuna)
├── scripts/             yordamchi skriptlar
├── stream_launcher.py   MediaMTX chaqiradigan yupqa qobiq (ildizda turishi shart)
│
├── Dockerfile           backend + MediaMTX + FFmpeg — bitta image
├── docker-compose.yml   ishga tushirish retsepti
├── .env.example         barcha sozlamalar izohlari bilan
├── docs/                hujjatlar (shu fayl ham)
└── data/                O'ZGARUVCHAN MA'LUMOT (volume) — pastda batafsil
```

Qatlamlar ataylab ajratilgan: `app/` MediaMTX bilan faqat
`from media import sync` orqali gaplashadi. Ertaga MediaMTX o'rniga boshqa
dvijok qo'yilsa, faqat `media/` o'zgaradi.

### data/ — eng qimmat papka

| Fayl | Nima | Yo'qolsa nima bo'ladi |
|---|---|---|
| `cameras.db` | SQLite: kameralar, foydalanuvchilar, hodisalar, statistika | hamma sozlama ketadi |
| `secret.key` | kamera parollarini ochadigan kalit | **parollar tiklanmaydi** — kameralarni qayta kiritish kerak |
| `mediamtx.yml` | avto-yaratiladi, tegilmaydi | o'zi qayta yoziladi (zarari yo'q) |
| `nigoh.log` | JSON hodisalar jurnali | tarix ketadi (zarari kam) |
| `mediamtx.log` | video dvijok logi | tarix ketadi (zarari kam) |

**Zaxira = shu papkani arxivlash.** Boshqa hech narsa kerak emas.

---

## 6. Baza: jadvallar

| Jadval | Nima saqlaydi |
|---|---|
| `cameras` | kameralar: nom, hudud, koordinata, IP, shifrlangan parol, kodek, o'lcham, qaysi tugun |
| `admins` | foydalanuvchilar: login, parol hash'i, rol (`admin`/`operator`) |
| `user_regions` | operator qaysi hududlarni ko'radi |
| `sessions` | kirish sessiyalari (12 soat) |
| `nodes` | MediaMTX tugunlari (bir nechta server bo'lsa) |
| `events` | media hodisalari: oqim muzladi/tiklandi, restart (30 kun) |
| `stats_region`, `stats_event` | dashboard tarixi: onlayn grafigi, uzilishlar (30 kun) |

Sxema o'zi migratsiya bo'ladi: yangi versiya eski bazani ochsa,
yetishmagan ustunlarni o'zi qo'shadi. "Bazani qo'lda yangilash" degan
tushuncha yo'q.

---

## 7. Xavfsizlik: to'rt qavat

**1-qavat. Kamera parollari** bazada Fernet shifrida yotadi (kalit —
`secret.key`). Ochiq holda faqat MediaMTX'ga RTSP manzil yasashda
ishlatiladi; brauzerga **hech qachon** qaytmaydi (admin panelda ham `•••`).

**2-qavat. Kirish.** Admin paroli qaytarilmas scrypt hash. Sessiya —
12 soatlik httponly cookie. Rollar: `admin` hammasini boshqaradi,
`operator` faqat biriktirilgan hududlarni ko'radi. `PUBLIC_VIEW=0`
qilinsa anonim odam umuman hech narsa ko'rmaydi.

**3-qavat. Oqim chiptalari.** Video portlari (8888/8889) ochiq bo'lsa ham
himoyalangan: MediaMTX **har bir** tomosha so'rovini backend'dan
tekshirtiradi. Backend faqat o'zi bergan imzoli, 1 soatlik, aynan shu
kameraga bog'langan chiptani qabul qiladi. Chiptasiz yo'l nomini bilgan
odam ham videoni ocholmaydi. Backend o'chiq bo'lsa MediaMTX hammani rad
etadi (yopiq holatda xavfsiz).

**4-qavat. Ichki portlar.** MediaMTX'ning boshqaruv API'si (9997) va
metrics (9998) faqat 127.0.0.1 da — tashqaridan umuman ko'rinmaydi.

---

## 8. O'z-o'zini boshqaradigan fon xizmatlari

Serverda uch "qorovul" doim aylanib turadi — shuning uchun qo'lda deyarli
hech narsa qilinmaydi:

**health (har 60 s).** Har kamera IP:portiga arzon TCP tekshiruv
(millisekundlar, trafik nol). Natija: xaritada yashil/qizil nuqta,
`last_seen`, Telegram xabari. Takror manzillar birlashtiriladi: 2000
kamera 40 NVR'da bo'lsa — 40 ta tekshiruv xolos.

**reconciler (har 30 s).** Uch ish: (1) bazadagi kerakli holatni
MediaMTX'dagi haqiqiy holat bilan solishtirib farqni tuzatadi — kamera
qo'shdingiz, 30 soniyada ishlaydi, restart yo'q; (2) lokal MediaMTX
yiqilgan bo'lsa qayta ishga tushiradi; (3) faol oqimlarning bayt hisobini
kuzatadi — 30 soniyada bitta bayt kelmagan oqim "muzlagan" (`stalled`)
deb belgilanadi. TCP tekshiruv buni ko'rmaydi: registrator portga javob
beraveradi, lekin kanal tasvir bermay qolgan bo'ladi.

**stats (har 5 daqiqa).** Hudud kesimida nechta kamera onlayn edi —
dashboard grafigi shu yozuvlardan chiziladi. 30 kundan eskisi o'chadi.

Uchchalasining natijasi bitta maydonga jamlanadi — har kameradagi
**`state`**: `online / offline / stalled / unknown / disabled`.

---

## 9. Kodek siyosati: nega o'girish kam

O'girish (H.265→H.264) qimmat: har oqimga ~400 MB xotira, GPU sessiyasi
(GeForce'da jami 8 ta!). Shuning uchun tartib bunday:

| Holat | Nima bo'ladi | Narxi |
|---|---|---|
| Kamera H.264 | MediaMTX borligicha uzatadi | deyarli nol |
| Kamera H.265 + brauzer o'qiy oladi (Chrome/Edge/Safari) | xom holda HLS orqali | deyarli nol |
| Kamera H.265 + brauzer o'qiy olmaydi (Firefox) | FFmpeg o'giradi | GPU + xotira |
| "Tez ochilsin" belgisi | doimiy qisqa-GOP o'girish (1 s da ochiladi) | doimiy resurs |

Sayt brauzerning imkoniyatini o'zi aniqlab (`hevc=1`), eng arzon yo'lni
tanlaydi. Amalda o'girish faqat zaxira bo'lib qoladi.

---

## 9½. Tashqi tizimga ulanish modeli (asosiy ishlatilish)

Nigoh alohida backend+frontend'li tizimga mikroservis bo'lib ulanadi.
U tizimning o'z foydalanuvchilari, o'z rollari, o'z super-admini bo'ladi —
Nigoh bunga aralashmaydi:

```
Foydalanuvchi ─▶ Ularning frontend ─▶ Ularning backend (o'z rollari)
                                          │ X-API-Key (server-to-server)
                                          ▼
                                        NIGOH
                                          ▼
                     MediaMTX ──▶ video to'g'ridan brauzerga (chipta bilan)
```

- Ularning backend'i `.env` dagi `NIGOH_API_KEY` bilan to'liq kiradi;
  `PUBLIC_VIEW=0` qo'yiladi — Nigoh'ga to'g'ridan kirgan begona hech
  narsa ko'rmaydi.
- Ruxsatni ular o'z rollarida tekshiradi, keyin Nigoh'dan **chiptali oqim
  manzilini** olib frontend'iga uzatadi. Video baribir MediaMTX'dan
  to'g'ridan boradi, lekin chiptasiz ochilmaydi — himoya Nigoh'da qoladi.
- Kamera nomi/kategoriyasi/joyi kabi metadata'ni ular o'z bazasida
  yuritishi mumkin (`nigoh_camera_id` bog'lash bilan); Nigoh uchun
  majburiysi — ulanish ma'lumotlari (IP, parol, yo'l).
- Nigoh'ning ichki `operator` roli va test UI bu rejimda ishlatilmaydi —
  ular Nigoh'ni mustaqil ishlatish va birinchi kunlarda kamera kiritish
  uchun turibdi.

Batafsil, kod namunasi bilan: [BACKEND.md](BACKEND.md).

## 10. Ko'p tugun: kameralar har xil joyda bo'lsa

Kameralar bir necha bino/shaharda bo'lsa, har joyga bitta MediaMTX
qo'yiladi (`nodes` jadvali):

```
      MARKAZ (Nigoh backend + asosiy MediaMTX)
        │ boshqaruv (9997, faqat markazga ochiq)
   ┌────┴─────────┐
Tugun-2 (B bino)  Tugun-3 (C shahar)
   │ RTSP lokal      │ RTSP lokal
 kameralar         kameralar
```

Foyda: kamera trafigi o'z binosida qoladi, magistralga faqat ayni damda
ko'rilayotgan oqim chiqadi. Brauzer videoni to'g'ridan-to'g'ri kerakli
tugundan oladi. Tugunga toza MediaMTX yetadi — konfiguratsiyani markaz
beradi (`GET /api/v1/admin/nodes/{id}/config`), yo'llarini API orqali
o'zi boshqaradi, salomatligini kuzatadi (`online/degraded/offline`).

Cheklov: o'girish faqat markazda ishlaydi — uzoq tugun kameralarini
H.264 da tuting.

---

## 11. Docker paketi: nega aynan shunday

**Nega bitta konteyner** (backend + MediaMTX + FFmpeg birga)? Ikki sabab:
o'girish launcher'i MediaMTX turgan mashinada ishlashi shart, va
reconciler MediaMTX jarayonini o'zi kuzatib qayta ko'taradi. Ajratilsa
shu ikkala mexanizm buziladi. Tashqaridan baribir bitta mikroservis.

**Nega host tarmog'i** (`network_mode: host`)? WebRTC video UDP orqali
yuradi va o'z IP'sini e'lon qiladi — port map qilinsa chalkashadi. Host
rejimida hammasi to'g'ridan ishlaydi.

**Nega `/data` volume?** Kod (image) va ma'lumot (volume) ajratilgan:
`docker compose up -d --build` bilan istalgan payt yangilaysiz — kameralar,
parollar, tarix joyida qoladi.

Yangi versiya chiqarish jarayoni:

```
kod o'zgardi → git commit → serverda: git pull (yoki papkani ko'chirish)
            → docker compose up -d --build   # ~1 daqiqa, ma'lumot saqlanadi
```

---

## 12. Kundalik amallar (shpargalka)

| Nima kerak | Buyruq / manzil |
|---|---|
| Ishga tushirish | `docker compose up -d --build` |
| Loglarni ko'rish | `docker logs nigoh` yoki `data/nigoh.log` (JSON) |
| Salomatlik | `GET /api/v1/admin/status` yoki brauzerda `/#dash` |
| Admin parolini almashtirish | `docker exec nigoh python main.py --admin-parol Yangi123` |
| Operator ochish | `POST /api/v1/admin/users` (`role: operator`, `regions: [...]`) |
| Zaxira | `tar czf zaxira.tar.gz data/` |
| API hujjati | `http://SERVER:8010/docs` |
| Yangilash | `docker compose up -d --build` |

---

## 13. Bir sahifalik xulosa

- **Nigoh = boshqaruv qatlami (FastAPI) + video dvijok (MediaMTX).**
- Baza — haqiqat manbai; MediaMTX unga har 30 soniyada moslanadi;
  tizim o'zini o'zi tiklaydi.
- Video backend orqali o'tmaydi; resurs kameralar soniga emas,
  **tomoshabinlar soniga** bog'liq.
- Xavfsizlik: shifrlangan parollar → rollar → oqim chiptalari →
  yopiq ichki portlar.
- Hamma qimmat narsa `data/` papkasida — zaxira shu.
- Dasturchilarga bitta eshik: `/api/v1` + `/docs`. MediaMTX — ichki ish,
  unga hech kim tegmaydi.

# Nigoh — o'lchovlar jurnali

Har o'zgarishdan keyin shu yerga yoziladi — keyingi o'zgarish regressiya
keltirsa darhol ko'rinadi. Maqsadlar mikroservis rejasidan.

| Ko'rsatkich | Qanday olinadi | Maqsad |
|---|---|---|
| Plitka ochilish vaqti | devor plitkasidagi raqam | < 2 s (issiq: < 1 s) |
| `/api/v1/streams` javob vaqti | 64 id bilan | < 1 s |
| Health sweep davomiyligi | `/health` → `health.duration_ms` | < 30 s |
| Egress | `/health` → `egress_mbps` | NIC 80% dan past |
| Brauzer xotirasi | Shift+Esc | 64 plitkada < 3 GB |
| Snapshot tsikli | `data/nigoh.log` → `snapshots cycle` | < 60 s |

## O'lchovlar

| Sana | Muhit | Ko'rsatkich | Qiymat | Izoh |
|---|---|---|---|---|
| 2026-08-19 | dev (Windows, manual kameralar) | /streams 67 id | 5 ms | tarmoqsiz, funksiya darajasida |
| 2026-08-19 | dev (soxta NVR probe) | skan birinchi natijasi | 304 ms | SSE orqali, 8 parallel |
| | serverda to'ldiriladi | plitka ochilish (sovuq/issiq) | | 2.1 GOP sinovidan keyin |
| | serverda to'ldiriladi | health sweep, real kameralar | | |

## 5000 kamera (2026-08-21, dev Windows, 80 NVR × 64 kanal)

Sintetik baza + haqiqiy MediaMTX v1.20.1. Python tomoni bemalol
ko'taradi — cheklov MediaMTX'ning yo'l boshqaruvida edi.

| Amal | Vaqt |
|---|---|
| `cameras_for_mediamtx` (5000 Fernet decrypt) | 51 ms |
| `desired_paths` | 14 ms |
| `GET /api/v1/cameras` (5000 ta, 1202 KB) | 85 ms |
| `GET /api/v1/cameras?bbox=` (kichik hudud) | 12 ms |
| `GET /api/v1/cameras/status?all=1` | 65 ms |
| `GET /api/v1/admin/cameras?limit=100` | 7 ms |
| health sweep manzillari (5000 kamera → 80 NVR) | 79 ta tekshiruv |

### Nega yo'llar talab bo'yicha yaratiladi

MediaMTX har `paths/add` so'roviga butun konfiguratsiyani qayta yuklaydi,
shuning uchun bitta yo'l qo'shish narxi mavjud yo'llar soniga chiziqli
o'sadi — jami narx kvadratik:

| MediaMTX'dagi yo'llar | Bitta yo'l qo'shish |
|---|---|
| 0 | 9,4 ms |
| 500 | 70 ms |
| 1900 | 167 ms |
| 2400 | 297 ms |

5000 kamera = 10001 yo'l (asosiy + sub + shablon). Hammasini oldindan
ro'yxatga olish taxminan **1,5-2 soat** oladi, MediaMTX xotirasi 1601
yo'lda allaqachon 116 MB, va MediaMTX har qayta ishga tushganda hammasi
boshidan boshlanadi.

Shuning uchun `desired_paths` faqat DOIMIY yo'llarni qaytaradi (o'girish
shabloni + `always_on` + ishlatilayotganlar), qolgani `ensure_path` bilan
ko'rish so'ralgan payt yaratiladi:

| | Oldin | Endi |
|---|---|---|
| Birinchi sinxron | ~1,5-2 soat | **0,0 s** |
| Tinch tsikl (har 30 s) | 21 HTTP so'rov + 10001 yo'l | **6 ms** |
| Kamera ochilishi (`ensure_path`) | 258 ms | **8,4 ms** |
| MediaMTX'dagi yo'llar | 10001 | ko'rilayotganlar soni |

`/health` dagi `managed` — ayni damda ro'yxatda turgan yo'llar. U kamera
soniga emas, tomoshabinlar soniga qarab o'sishi kerak.

### Uzilishlar tahlili (5000 kamera, 30 000 hodisa, 24 soat)

| Endpoint | Vaqt | Javob |
|---|---|---|
| `/admin/uptime` (top 100) | 59 ms | 18 KB |
| `/admin/uptime?limit=5000` | 93 ms | 892 KB |
| `/admin/uptime?group_by=region` | 55 ms | 2 KB |
| `/admin/uptime?group_by=nvr` | 54 ms | 11 KB |
| `/admin/outages/hourly` (park) | 20 ms | — |
| `/admin/outages/hourly?hours=720` | 20 ms | — |

O'tishlar bitta so'rov bilan o'qiladi (`idx_events_slug_ts`), kamera
kesimida emas — aks holda 5000 ta alohida so'rov bo'lardi. Guruh
ko'rinishi javobi 2-11 KB: kundalik ish uchun aynan shuni so'rang,
`limit=5000` faqat eksport uchun.

### Eski o'rnatishdan yangilash

Oldingi versiya MediaMTX'ga minglab yo'l yozib qo'ygan bo'lsa, ular
tozalanadi, lekin sekin (har o'chirish ham reload) — `/api/v1/admin/status`
va `/api/v1/admin/nodes` da `pending_paths` noldan katta turadi va jurnalda
`paths_bloated` ogohlantirishi chiqadi. Tezroq yo'l — **MediaMTX'ni bir
marta qayta ishga tushirish**: API orqali qo'shilgan yo'llar faylga
yozilmaydi (tekshirildi: 2051 → 0), kerakli yo'l esa ko'rilganda o'zi
tiklanadi.

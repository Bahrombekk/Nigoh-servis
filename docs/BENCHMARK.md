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

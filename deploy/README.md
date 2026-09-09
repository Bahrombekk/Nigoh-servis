# deploy/

## nginx

Nginx konfiguratsiyasi bu yerda **fayl bo'lib yotmaydi** — u
`scripts/nginx_conf.py` bilan yaratiladi:

```bash
cd /opt/nigoh
python scripts/nginx_conf.py > /etc/nginx/sites-available/negoh.conf
ln -sf /etc/nginx/sites-available/negoh.conf /etc/nginx/sites-enabled/
nginx -t && systemctl reload nginx
```

Domen `.env` dagi `MEDIA_BASE` dan olinadi; boshqasi kerak bo'lsa
`--domain negoh.das-uty.uz`. Sertifikat hali olinmagan bo'lsa (certbot
ishlamagan) — `--no-ssl`, keyin certbot'dan so'ng qaytadan yarating.

### Nima uchun qo'lda yozilgan namuna emas

`/media/hls/` blokida ikkita narsa bo'lishi **shart**:

* `auth_request /_hlsauth;` — tomoshabin chiptasini nginx tekshiradi.
  Bo'lmasa slug'ni bilgan har kim kamerani ko'ra oladi.
* `proxy_set_header Authorization "Bearer <kalit>";` — MediaMTX'ning
  "CDN kaliti". Bo'lmasa MediaMTX **sessiyali** rejimda ishlaydi: manba
  qisqa uzilganda HLS muxeri yo'q qilinadi, sessiya o'ladi va tomoshabin
  **doimiy 401** oladi.

Kalit MediaMTX tomonda (`hlsCDNSecret`) va nginx tomonda aynan bir xil
bo'lishi kerak. Ilgari u `.env` da qo'lda yoziladigan sozlama edi va
ikkala blok qo'lda ko'chirilardi — natijada 80-portdagi blok to'g'ri,
443-portdagi esa eski namunadan olingan (auth_request ham, Bearer ham
yo'q) bo'lib qoldi. Muammo shuning uchun faqat HTTPS'da ko'rindi:

```
https://.../media/hls/<slug>/video1_stream.m3u8?session=...&token=...  -> 401
```

Lokalda `MEDIA_BASE` bo'sh bo'lgani uchun brauzer videoni to'g'ridan
MediaMTX portidan oladi — nginx umuman qatnashmaydi va muammo bilinmaydi.

Endi kalit `secret.key` dan avtomatik hosil bo'ladi va ikkala blok ham
bitta skriptdan chiqadi — mos kelmasligi mumkin emas.

### Tekshirish

Manzilda `session=` ko'rinsa — Bearer sarlavhasi yetib bormayapti:

```bash
curl -s "https://negoh.das-uty.uz/media/hls/<slug>/index.m3u8?token=<chipta>" | grep -c session
# 0 bo'lishi kerak
```

Servis jurnalida ham ko'rinadi: `hls_bearer_yoq` ogohlantirishi.

## yangilash.sh

Pull-based deploy (cron'dan). Nginx'ga tegmaydi — konfiguratsiya
o'zgarganda yuqoridagi buyruqni qo'lda bajaring.

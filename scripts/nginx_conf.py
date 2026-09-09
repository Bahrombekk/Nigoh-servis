#!/usr/bin/env python3
"""Nigoh — nginx konfiguratsiyasini TAYYOR holda chiqaradi.

Nima uchun skript, qo'lda ko'chiriladigan namuna emas:

HLS "CDN kaliti" ikkita joyda AYNAN bir xil bo'lishi shart — MediaMTX
tomonda (`hlsCDNSecret`, uni endi servis avtomatik qo'yadi) va nginx
tomonda (`proxy_set_header Authorization "Bearer ..."`). Bundan tashqari
nginx tomoshabin chiptasini o'zi tekshirishi kerak
(`auth_request /_hlsauth`), aks holda slug'ni bilgan har kim kamerani
ko'ra oladi.

Qo'lda ko'chirilgan namunada aynan shu buzildi: 80-portdagi blok to'g'ri
edi, HTTPS (443) bloki esa docs/DEPLOY.md dagi eski qisqa namunadan
olingan bo'lib, unda na `auth_request`, na Bearer bor edi. Natijada
HTTPS orqali kelgan HLS so'rovlarida MediaMTX sessiyali rejimda qoldi va
manba har uzilganda tomoshabin DOIMIY 401 oldi:

    .../media/hls/<slug>/video1_stream.m3u8?session=...&token=...  -> 401

Lokalda muammo ko'rinmasdi, chunki MEDIA_BASE bo'sh bo'lganda brauzer
videoni to'g'ridan MediaMTX portidan oladi — nginx umuman qatnashmaydi.
Ana o'sha "lokalda ishlaydi, serverda ishlamaydi" farqi shu skript bilan
yopiladi: 80 ham, 443 ham bitta manbadan yaratiladi.

Ishlatish (serverda, loyiha ildizidan):

    python scripts/nginx_conf.py > /etc/nginx/sites-available/negoh.conf
    nginx -t && systemctl reload nginx

Domen `.env` dagi MEDIA_BASE dan olinadi; boshqasi kerak bo'lsa
`--domain`. Sertifikat hali yo'q bo'lsa (certbot ishlamagan) `--no-ssl`
bilan faqat 80-portli variant chiqadi.
"""
import argparse
import os
import sys
from pathlib import Path
from urllib.parse import urlsplit

BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))


def load_env(path: Path) -> None:
    """`.env` ni o'qiydi.

    Servis uni o'zi yuklamaydi (buni docker compose yoki systemd qiladi),
    lekin bu skript ishlab turgan servis bilan BIR XIL qiymatlarni
    ko'rishi shart — aks holda na port, na kalit to'g'ri chiqadi.
    Muhitda allaqachon bor qiymat ustun turadi.
    """
    if not path.exists():
        return
    for satr in path.read_text(encoding="utf-8").splitlines():
        satr = satr.strip()
        if not satr or satr.startswith("#") or "=" not in satr:
            continue
        kalit, _, qiymat = satr.partition("=")
        os.environ.setdefault(kalit.strip(), qiymat.strip().strip("\"'"))


def media_locations(domain: str, api_port: int, hls_port: int,
                    webrtc_port: int, secret: str, scheme: str) -> str:
    """/media/... bloklari — 80 va 443 uchun aynan bir xil matn."""
    return f"""
    # ---- HLS video -----------------------------------------------------
    location /media/hls/ {{
        # Tomoshabin chiptasi SHU YERDA tekshiriladi (quyidagi /_hlsauth).
        # Pastdagi Bearer sarlavhasi bilan kelgan so'rovni MediaMTX
        # shartsiz o'tkazadi va bizning auth ilgagimizni umuman
        # chaqirmaydi — tekshiruv nginx'ga ko'chmasa slug'ni bilgan har
        # kim kamerani ko'ra olardi.
        auth_request /_hlsauth;

        # "CDN kaliti" — MediaMTX bu sarlavhani ko'rsa SESSIYASIZ ishlaydi
        # va manzillarga na `session=`, na `token=` qo'shadi. Sessiyali
        # rejimda manba qisqa uzilsa MediaMTX HLS muxerini yo'q qiladi,
        # muxer bilan sessiya ham o'ladi va mijoz DOIMIY 401 oladi.
        # Qiymat secret.key'dan hosil qilinadi; MediaMTX tomonda
        # `hlsCDNSecret` xuddi shunga qo'yiladi (media/sync.py).
        proxy_set_header Authorization "Bearer {secret}";

        proxy_pass http://127.0.0.1:{hls_port}/;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_buffering off;

        # KESH O'CHIRILADI. MediaMTX master pleylistga
        # `Cache-Control: public, max-age=30` qo'yadi. Manba uzilganda
        # (kameralar RTSP sessiyasini har ~60 soniyada o'zlari uzadi)
        # muxer yo'q bo'ladi va o'lchov shuni ko'rsatdi:
        #
        #     video1_stream.m3u8  -> 401 (muxer yo'q)
        #     index.m3u8          -> 200, LEKIN BRAUZER KESHIDAN
        #
        # Ya'ni pleyerning "masterni qayta yuklab muxerni tiklash" qadami
        # MediaMTX'ga umuman yetmasdi — 30 soniya davomida keshdagi eski
        # javob qaytarilardi va tomoshabin o'sha 30 soniya qora ekran
        # ko'rardi. Segmentni keshlashning ham foydasi yo'q: jonli HLS'da
        # har segment bir marta so'raladi.
        #
        # Faqat Cache-Control almashtiriladi: MediaMTX'ning
        # `Access-Control-Allow-Origin` sarlavhasi o'z holida o'tadi
        # (uni `add_header` bilan takrorlash brauzerda "multiple values"
        # xatosini berardi).
        proxy_hide_header Cache-Control;
        add_header Cache-Control "no-store" always;

        # MediaMTX redirect'lari prefikssiz yoki http:// bilan kelishi
        # mumkin — doim to'liq manzilga keltiramiz, aks holda brauzer
        # Mixed Content deb bloklaydi.
        proxy_redirect ~^(?:https?://[^/]+)?/(?:media/hls/)?(.*)$ {scheme}://{domain}/media/hls/$1;
    }}

    # Chipta tekshiruvi — faqat ichki, tashqaridan chaqirilmaydi.
    # 204 — so'rov MediaMTX'ga o'tadi, 401 — rad etiladi.
    location = /_hlsauth {{
        internal;
        proxy_pass http://127.0.0.1:{api_port}/api/auth/hls;
        proxy_pass_request_body off;
        proxy_set_header Content-Length "";
        # Asl manzil va tomoshabin IP'si — chipta shu ikkisiga tekshiriladi.
        proxy_set_header X-Original-URI $request_uri;
        proxy_set_header X-Viewer-IP $remote_addr;
    }}

    # ---- WebRTC signal (WHEP) ------------------------------------------
    location /media/whep/ {{
        proxy_pass http://127.0.0.1:{webrtc_port}/;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_redirect ~^(?:https?://[^/]+)?/(?:media/whep/)?(.*)$ {scheme}://{domain}/media/whep/$1;
    }}
"""


def api_locations(api_port: int, scheme: str) -> str:
    return f"""
    # ---- SSE (jonli hodisalar va skan natijalari) -----------------------
    # Buferlash O'CHIQ bo'lishi SHART: aks holda hodisalar nginx buferida
    # turib qoladi va "jonli" oqim daqiqalab kechikadi.
    location ~ ^/api(/v1)?/events$ {{
        proxy_pass http://127.0.0.1:{api_port};
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto {scheme};
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 24h;
    }}
    location ~ ^/api(/v1)?/devices/scan/[^/]+/events$ {{
        proxy_pass http://127.0.0.1:{api_port};
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto {scheme};
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 1h;
    }}

    # ---- API + konsol (qolgan hamma narsa) ------------------------------
    location / {{
        proxy_pass http://127.0.0.1:{api_port};
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-Proto {scheme};
        proxy_set_header X-Forwarded-For $remote_addr;
    }}
"""


HEADER = """# Nigoh — nginx konfiguratsiyasi.
# AVTOMATIK YARATILGAN: python scripts/nginx_conf.py
#
# Qo'lda tahrirlamang. HLS "CDN kaliti" bu yerda va MediaMTX
# konfiguratsiyasida AYNAN bir xil bo'lishi shart, tomoshabin chiptasi
# esa `auth_request` orqali tekshirilishi shart — qo'lda ko'chirishda
# aynan shu ikkisi HTTPS blokidan tushib qolgan edi va tomoshabin
# doimiy 401 olardi.
#
# O'rnatish:
#   python scripts/nginx_conf.py > /etc/nginx/sites-available/negoh.conf
#   ln -sf /etc/nginx/sites-available/negoh.conf /etc/nginx/sites-enabled/
#   nginx -t && systemctl reload nginx
#
# Diqqat: WebRTC media (UDP/TCP 8189) nginx'dan o'tmaydi — firewall'da
# to'g'ridan oching.
"""


def build(domain: str, api_port: int, hls_port: int, webrtc_port: int,
          secret: str, ssl: bool, cert_dir: str) -> str:
    api80 = api_locations(api_port, "$scheme")
    if not ssl:
        media80 = media_locations(domain, api_port, hls_port, webrtc_port,
                                  secret, "http")
        return HEADER + f"""
server {{
    listen 80;
    server_name {domain};
{api80}{media80}}}
"""
    media443 = media_locations(domain, api_port, hls_port, webrtc_port,
                               secret, "https")
    return HEADER + f"""
# 80 -> 443. ACME (certbot) tekshiruvi redirect'dan oldin turadi.
server {{
    listen 80;
    server_name {domain};

    location /.well-known/acme-challenge/ {{
        root /var/www/html;
    }}
    location / {{
        return 301 https://$host$request_uri;
    }}
}}

server {{
    listen 443 ssl;
    listen [::]:443 ssl;
    http2 on;
    server_name {domain};

    ssl_certificate     {cert_dir}/{domain}/fullchain.pem;
    ssl_certificate_key {cert_dir}/{domain}/privkey.pem;
{api_locations(api_port, "https")}{media443}}}
"""


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Nigoh uchun tayyor nginx konfiguratsiyasi.",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--domain", default="",
                    help="Domen. Berilmasa .env dagi MEDIA_BASE dan olinadi.")
    ap.add_argument("--no-ssl", action="store_true",
                    help="Faqat 80-port (sertifikat hali yo'q bo'lsa).")
    ap.add_argument("--cert-dir", default="/etc/letsencrypt/live",
                    help="Sertifikatlar katalogi (standart: certbot).")
    ap.add_argument("--env", default=str(BASE_DIR / ".env"),
                    help="Sozlamalar fayli (standart: ildizdagi .env).")
    args = ap.parse_args()

    # Chiqish har doim UTF-8: fayl Linux serverga ko'chiriladi, Windows
    # konsolining cp866/cp1251 kodlashi izohlarni buzib yuborardi.
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

    load_env(Path(args.env))

    # Import .env yuklangandan KEYIN: kalit ham, ma'lumotlar katalogi ham
    # servisdagi bilan bir xil bo'lishi kerak.
    from core import security

    domain = args.domain.strip()
    media_base = os.environ.get("MEDIA_BASE", "").strip()
    if not domain and media_base:
        domain = urlsplit(media_base).hostname or ""
    if not domain:
        ap.error("Domen topilmadi: .env da MEDIA_BASE yo'q — --domain bering.")

    ssl = not args.no_ssl
    if ssl and media_base and urlsplit(media_base).scheme != "https":
        print("# DIQQAT: .env dagi MEDIA_BASE https:// emas, konfiguratsiya "
              "esa HTTPS uchun yaratildi.\n"
              f"# MEDIA_BASE=https://{domain}/media qilib qo'ying, aks holda "
              "brauzer videoni\n# mixed-content deb bloklaydi.", file=sys.stderr)

    sys.stdout.write(build(
        domain=domain,
        api_port=int(os.environ.get("PORT", "8010")),
        hls_port=int(os.environ.get("HLS_PORT", "8888")),
        webrtc_port=int(os.environ.get("WEBRTC_PORT", "8889")),
        secret=security.hls_cdn_secret(),
        ssl=ssl,
        cert_dir=args.cert_dir.rstrip("/"),
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

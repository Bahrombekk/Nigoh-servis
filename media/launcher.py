"""Nigoh — bitta kamera oqimini ochadigan yordamchi (media paketi).

MediaMTX buni kimdir kamerani ko'rmoqchi bo'lganda ildizdagi
`stream_launcher.py` orqali chaqiradi:

    python stream_launcher.py <slug>

Skript bazadan kamerani topadi va FFmpeg'ni ishga tushiradi. Shu tufayli
`mediamtx.yml` ichida kameralar ro'yxati ham, parollar ham saqlanmaydi —
1000 ta kamera bo'lsa ham konfiguratsiya o'zgarmaydi.
"""
import concurrent.futures
import os
import socket
import subprocess
import sys
import time

from core import security
from core.db import get_db
from core.rtsp_probe import build_rtsp_url

from . import sync
from .sync import (RTSP_PORT, SUB_SUFFIX, ffmpeg_path, has_nvenc,
                   relay_args, transcode_args)

# Ikki vazifa bor:
#
#   `<kamera>_h264`  — O'GIRISH. Manba MediaMTX'dagi xom yo'l, natija
#                      brauzer o'qiy oladigan H.264.
#   `<kamera>`       — RELAY (qayta kodlashsiz uzatish). Manba KAMERANING
#                      o'zi. Bu faqat RTSP_VIA_FFMPEG=1 bo'lganda
#                      chaqiriladi; sababi relay_args() izohida —
#                      MediaMTX RTSP keepalive yubormaydi va `timeout=60`
#                      e'lon qilgan kamera har 60 soniyada uzadi.
TRANSCODE_SUFFIX = "_h264"

# Kamera o'chiq bo'lsa MediaMTX (runOnInitRestart) bizni darhol qayta
# ishga tushiraveradi — har 5 soniyada bekorga FFmpeg ochilib, log to'lib
# ketadi. Shu pauzalar urinishlar orasini kengaytiradi.
OFFLINE_RETRY_DELAY = 30.0   # kamera tarmoqdan javob bermasa
CRASH_RETRY_DELAY = 10.0     # FFmpeg darhol o'lib qolsa (manba hali yo'q)


def camera_reachable(ip: str, port: int) -> bool:
    """Kameraga arzon TCP tekshiruv — RTSP ochmasdan tirikligini bilamiz."""
    try:
        sock = socket.create_connection((ip, port or 554), timeout=3)
        sock.close()
        return True
    except OSError:
        return False


def load_camera(slug: str):
    with get_db() as db:
        row = db.execute(
            "SELECT slug, ip, port, username, password_enc, rtsp_path, "
            "sub_path, transcode, enabled FROM cameras WHERE slug = ?",
            (slug,),
        ).fetchone()
    return row


def _ready_set() -> set[str]:
    """MediaMTX'da HOZIR kadr berayotgan yo'llar — API orqali (tez, ffprobe
    yo'q). `ready` + trek bor + bayt kelmoqda bo'lsa, yo'l ishlayapti."""
    try:
        d = sync._api("GET", "/v3/paths/list?itemsPerPage=1000")
    except Exception:
        return set()
    out = set()
    for i in (d or {}).get("items", []):
        if i.get("ready") and i.get("tracks") and i.get("bytesReceived"):
            out.add(i["name"])
    return out


def _warm(names: list[str], port: int, token: str, timeout: float = 6.0) -> None:
    """Sovuq relaylarni parallel ISITADI: bitta kadr o'qib sourceOnDemand'ni
    yoqadi. Faqat tayyor bo'lmagan relaylar uchun chaqiriladi (tayyorlarini
    qayta ochib vaqt yo'qotmaymiz)."""
    exe = ffmpeg_path()
    if not exe or not names:
        return

    def touch(name: str) -> None:
        url = f"rtsp://127.0.0.1:{port}/{name}?token={token}"
        try:
            subprocess.run(
                [exe, "-hide_banner", "-loglevel", "error",
                 "-rtsp_transport", "tcp", "-timeout", "4000000",
                 "-i", url, "-frames:v", "1", "-f", "null", "-"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                timeout=timeout)
        except Exception:
            pass

    with concurrent.futures.ThreadPoolExecutor(
            max_workers=min(16, len(names))) as ex:
        list(ex.map(touch, names))


def run_wall(slug: str) -> int:
    """Devor (mozaika): bir necha kameraning LOKAL relay yo'lini bitta
    katakli oqimga birlashtirib MediaMTX'ga publish qiladi.

    Kamera bilan to'g'ridan ishlamaymiz — MediaMTX orqa xonda ushlab
    turgan `<slug>_sub` relayidan o'qiymiz (arxitektura: relay o'rtada).
    Faqat HOZIR kadr beradigan relaylar real katak bo'ladi; qolgani qora
    katak, shunda bitta o'lik kamera butun mozaikani yiqitmaydi.
    """
    from . import mosaic, walls
    key = slug[len("wall_"):]
    info = walls.wall_relays(key)
    if info is None:
        print(f"Devor topilmadi: {slug}", file=sys.stderr)
        return 3
    relays = info["relays"]
    if not any(relays):
        print(f"{slug}: birorta tirik kamera yo'q", file=sys.stderr)
        return 3
    token = security.internal_token()

    # Qaysi relay ayni damda kadr beradi. Tayyorlarini API'dan darhol
    # olamiz (ffprobe yo'q — tez); sovuqlarini isitamiz va qayta tekshiramiz.
    # xstack kadr bermagan kirishni abadiy kutgani uchun faqat kadr
    # berayotganlar real katak bo'ladi, qolgani qora.
    alive = sorted({n for n in relays if n})
    flowing = _ready_set() & set(alive)
    cold = [n for n in alive if n not in flowing]
    if cold:
        _warm(cold, RTSP_PORT, token)
        flowing = _ready_set() & set(alive)
    sources = [
        f"rtsp://127.0.0.1:{RTSP_PORT}/{n}?token={token}"
        if (n and n in flowing) else None
        for n in relays
    ]
    live = sum(1 for s in sources if s)
    if live == 0:
        print(f"{slug}: birorta relay tayyor emas", file=sys.stderr)
        time.sleep(CRASH_RETRY_DELAY)
        return 3

    exe = ffmpeg_path()
    if not exe:
        print("FFmpeg topilmadi — PATH ga qo'shing", file=sys.stderr)
        return 6
    dest = f"rtsp://127.0.0.1:{RTSP_PORT}/{slug}?token={token}"
    args = mosaic.mosaic_args(sources, cols=info["cols"], rows=info["rows"],
                              dst_url=dest, gpu=has_nvenc())
    print(f"{slug}: {info['cols']}x{info['rows']} mozaika · {live} tirik katak "
          f"({'GPU' if has_nvenc() else 'CPU'})", file=sys.stderr)
    started = time.monotonic()
    process = subprocess.Popen([exe] + args)
    try:
        code = process.wait()
        if code != 0 and time.monotonic() - started < 5:
            time.sleep(CRASH_RETRY_DELAY)
        return code
    except KeyboardInterrupt:
        process.terminate()
        try:
            return process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            return 1


def main() -> int:
    slug = (sys.argv[1] if len(sys.argv) > 1 else os.environ.get("MTX_PATH", "")).strip()
    if not slug:
        print("Kamera nomi berilmadi", file=sys.stderr)
        return 2

    # Devor mozaikasi — kamera emas, alohida tarmoq.
    if slug.startswith("wall_"):
        return run_wall(slug)

    ogirish = slug.endswith(TRANSCODE_SUFFIX)
    if ogirish:
        lookup = slug[: -len(TRANSCODE_SUFFIX)]
    else:
        # Relay: yo'l nomining o'zi.
        #
        # Bu yerda RTSP_VIA_FFMPEG ni QAYTA TEKSHIRMAYMIZ. Sababi: bu
        # skriptni MediaMTX ishga tushiradi, MediaMTX esa ilovadan oldin
        # ko'tarilgan bo'lishi mumkin va uning muhitida yangi
        # o'zgaruvchi bo'lmaydi — natijada relay jim ishlamay qolardi
        # (o'lchovda aynan shu bo'ldi: "command exited with code 7"
        # aylanib turdi). Qaror YO'L KONFIGURATSIYASIDA: MediaMTX
        # launcher'ni faqat biz `runOnDemand` qilib sozlagan yo'l uchun
        # chaqiradi, shablon yo'l esa faqat `_h264` ga mos keladi.
        lookup = slug

    # `<kamera>_sub` (yoki `<kamera>_sub_h264`) — sub-oqim. Bazada bunday
    # slug yo'q (sub asosiy kameraning ikkinchi oqimi), shuning uchun
    # kamera asosiy slug bo'yicha qidiriladi.
    sub = lookup.endswith(SUB_SUFFIX)
    db_slug = lookup[: -len(SUB_SUFFIX)] if sub else lookup

    row = load_camera(db_slug)
    if row is None:
        print(f"Kamera topilmadi: {db_slug}", file=sys.stderr)
        return 3
    if not row["enabled"]:
        print(f"Kamera o'chirilgan: {slug}", file=sys.stderr)
        return 4
    if not row["ip"]:
        print(f"Kamerada IP yo'q: {slug}", file=sys.stderr)
        return 5

    # Kamera tarmoqdan javob bermasa FFmpeg'ni ochishning ma'nosi yo'q —
    # baribir 404 bilan o'ladi. Kutamiz, keyin MediaMTX qayta chaqiradi.
    if not camera_reachable(row["ip"], row["port"]):
        print(f"{slug}: kamera javob bermayapti ({row['ip']}:{row['port'] or 554}) — "
              f"{int(OFFLINE_RETRY_DELAY)} soniyadan keyin qayta uriniladi",
              file=sys.stderr)
        time.sleep(OFFLINE_RETRY_DELAY)
        return 8

    # Manba — kameraning o'zi emas, MediaMTX'dagi xom yo'l: kamera bilan
    # bitta ulanish yetadi, uni ham xom, ham o'girilgan ko'rinishda beramiz.
    # Ichki chipta shart: auth endi IP'ga qarab ruxsat bermaydi (proksi
    # ortida hamma 127.0.0.1 bo'lib ko'rinadi).
    auth = f"?token={security.internal_token()}"
    destination = f"rtsp://127.0.0.1:{RTSP_PORT}/{slug}{auth}"
    if ogirish:
        # Manba — MediaMTX'dagi xom yo'l: kamera bilan bitta ulanish
        # yetadi, uni ham xom, ham o'girilgan ko'rinishda beramiz.
        source = f"rtsp://127.0.0.1:{RTSP_PORT}/{lookup}{auth}"
    else:
        # Relay — manba kameraning o'zi.
        yol = (row["sub_path"] if sub else row["rtsp_path"]) or "/"
        if sub and not row["sub_path"]:
            print(f"{slug}: kamerada sub yo'l yo'q", file=sys.stderr)
            return 5
        source = build_rtsp_url(row["ip"], row["port"], yol,
                                row["username"] or "",
                                security.decrypt(row["password_enc"]))

    exe = ffmpeg_path()
    if not exe:
        print("FFmpeg topilmadi — PATH ga qo'shing", file=sys.stderr)
        return 6

    if ogirish:
        args = transcode_args(source, destination, gpu=has_nvenc())
        print(f"{slug}: H.264 ga o'girilmoqda ({'GPU' if has_nvenc() else 'CPU'})",
              file=sys.stderr)
    else:
        args = relay_args(source, destination)
        print(f"{slug}: kameradan FFmpeg orqali uzatilmoqda (qayta kodlashsiz)",
              file=sys.stderr)

    # FFmpeg shu jarayonning o'rnini egallaydi — MediaMTX uni to'g'ridan
    # to'g'ri boshqaradi (to'xtatish signali ham to'g'ri yetib boradi).
    started = time.monotonic()
    process = subprocess.Popen([exe] + args)
    try:
        code = process.wait()
        # Darhol o'lib qoldi (masalan, xom yo'l hali tayyor emas) — restart
        # sikli tezlashib ketmasin, ozroq nafas olamiz.
        if code != 0 and time.monotonic() - started < 5:
            time.sleep(CRASH_RETRY_DELAY)
        return code
    except KeyboardInterrupt:
        process.terminate()
        try:
            return process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            return 1


if __name__ == "__main__":
    sys.exit(main())

"""Kamera kadrni TENG ORALIQDA beryaptimi — "qotish" shikoyatining manbasi.

Sekundlik hisoblagich bu savolga javob bermaydi: kamera bir sekundda 25
kadrni bir zumda tashlab, keyin jim tursa ham "25 kadr/s" bo'lib ko'rinadi.
Tomoshabin esa aynan o'sha jimlikni qotish deb ko'radi. Shu skript
kadrlarning KELISH vaqtini o'lchaydi — oraliqning mediani va eng
kattasini.

Zanjirda bizning hech narsamiz yo'q: ffprobe to'g'ridan kameraga ulanadi.
Ya'ni natija "MediaMTX aybdormi yoki kamerami" degan savolni yopadi.

    python scripts/kadr_oraligi.py 26 27
    python scripts/kadr_oraligi.py --hammasi --sekund 60
    python scripts/kadr_oraligi.py 26 --sub        # ikkinchi oqim

Baho ustuni: eng katta oraliq pleyer buferidan (JITTER_MS, 1 s) uzun
bo'lsa tomoshabin qotish ko'radi. Buni kodda emas, kameraning o'zida
tuzatiladi — bitreyt rejimi (VBR -> CBR), I-kadr oralig'i, "smoothing".

DIQQAT: RTSP transporti TCP. UDP'ga o'tkazish vasvasasi bo'ladi ("TCP
portlashni keltirib chiqaryapti-ku"), lekin shu o'rnatmada o'lchandi va
UDP'da ffmpeg tinmay `RTP: missed N packets` berdi — ya'ni qotish
o'rniga tasvir buzilishi. TCP to'g'ri tanlov.
"""
import argparse
import subprocess
import sys
import threading
import time

sys.path.insert(0, "/app" if __import__("os").path.isdir("/app/core") else ".")

from core import security  # noqa: E402
from core.db import get_db  # noqa: E402
from core.rtsp_probe import build_rtsp_url  # noqa: E402
from media.sync import ffmpeg_path  # noqa: E402

ISITISH = 3.0          # birinchi soniyalarda bufer to'kiladi — hisobga olinmaydi
PLEYER_BUFERI = 1.0    # debug-ui/app.js dagi JITTER_MS (soniyada)


def olcha(url: str, sekund: int) -> dict:
    """Kadrlarning kelish oralig'i (ms). Bo'sh lug'at — kadr kelmadi."""
    cmd = [ffmpeg_path().replace("ffmpeg", "ffprobe"),
           "-hide_banner", "-loglevel", "error", "-rtsp_transport", "tcp",
           "-select_streams", "v:0", "-show_entries", "packet=pts_time",
           "-of", "compact=p=0:nk=1", "-i", url]
    p = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                         stderr=subprocess.DEVNULL, text=True, bufsize=1)
    kelish: list[float] = []

    def oqi() -> None:
        for satr in p.stdout:            # har satr — bitta video kadr
            if satr.strip():
                kelish.append(time.monotonic())

    threading.Thread(target=oqi, daemon=True).start()
    time.sleep(sekund)
    p.kill()

    if len(kelish) < 10:
        return {}
    t0 = kelish[0]
    kelish = [t for t in kelish if t - t0 > ISITISH]
    if len(kelish) < 10:
        return {}
    oraliq = sorted(1000 * (b - a) for a, b in zip(kelish, kelish[1:]))
    n = len(oraliq)
    davom = kelish[-1] - kelish[0]
    # Bo'shliq deb pleyer buferidan uzun jimlikni sanaymiz — aynan
    # shunda brauzer quruq qoladi.
    boshliq = [v for v in oraliq if v > PLEYER_BUFERI * 1000]
    return {
        "kadr": n + 1,
        "fps": (n + 1) / davom,
        "p50": oraliq[n // 2],
        "p95": oraliq[int(n * 0.95)],
        "eng_katta": oraliq[-1],
        "boshliq_soni": len(boshliq),
        "boshliq_sek": sum(boshliq) / 1000,
        "davom": davom,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("camera_ids", nargs="*", type=int)
    ap.add_argument("--sekund", type=int, default=45)
    ap.add_argument("--hammasi", action="store_true")
    ap.add_argument("--sub", action="store_true", help="ikkinchi (past) oqim")
    a = ap.parse_args()

    with get_db() as db:
        if a.hammasi:
            rows = db.execute(
                "SELECT * FROM cameras WHERE enabled = 1 AND ip != '' "
                "ORDER BY id").fetchall()
        else:
            q = ",".join("?" * len(a.camera_ids))
            rows = db.execute(
                f"SELECT * FROM cameras WHERE id IN ({q})",
                a.camera_ids).fetchall()
        rows = [dict(r) for r in rows]
        for r in rows:
            r["_parol"] = security.decrypt(r["password_enc"])

    print(f"har biri {a.sekund} s | oraliq ms da | bo'shliq = "
          f"{PLEYER_BUFERI:.0f} s dan uzun jimlik")
    print("   id ip                 fps   p50   p95  eng katta  bo'shliq  baho")
    for r in rows:
        yol = (r["sub_path"] if a.sub else r["rtsp_path"]) or r["rtsp_path"]
        url = build_rtsp_url(r["ip"], r["port"] or 554, yol,
                             r["username"] or "", r["_parol"])
        o = olcha(url, a.sekund)
        if not o:
            print(f"{r['id']:5} {r['ip']:16} kadr kelmadi")
            continue
        ulush = 100 * o["boshliq_sek"] / o["davom"]
        if o["eng_katta"] <= PLEYER_BUFERI * 1000:
            baho = "yaxshi"
        elif ulush < 10:
            baho = "sezilarli"
        else:
            baho = "YOMON - kamera sozlamasi"
        print(f"{r['id']:5} {r['ip']:16} {o['fps']:5.1f} {o['p50']:5.0f} "
              f"{o['p95']:5.0f} {o['eng_katta']:10.0f} "
              f"{o['boshliq_soni']:4} / {o['boshliq_sek']:4.1f}s  {baho}")


if __name__ == "__main__":
    main()

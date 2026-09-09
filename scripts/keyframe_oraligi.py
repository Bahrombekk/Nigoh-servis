"""Kameraning keyframe (IDR) oralig'ini o'lchaydi — sovuq ochilish narxi.

Talab bo'yicha ochiladigan yo'lda tomoshabin BIRINCHI keyframe'ni kutadi:
undan oldin HLS segmenti yopilmaydi va WebRTC dekodni boshlay olmaydi.
Ya'ni "kamera sekin ochilyapti" ning eng katta ulushi shu raqamda —
uni kodda emas, registrator/kamera sozlamasida (I Frame Interval)
tuzatiladi.

    python scripts/keyframe_oraligi.py 26 27 30
    python scripts/keyframe_oraligi.py --hammasi --sekund 20

Natijada: birinchi keyframe'gacha kutish va o'rtacha oraliq.
"""
import argparse
import subprocess
import sys

sys.path.insert(0, "/app" if __import__("os").path.isdir("/app/core") else ".")

from core import security  # noqa: E402
from core.db import get_db  # noqa: E402
from core.rtsp_probe import build_rtsp_url  # noqa: E402
from media.sync import ffmpeg_path  # noqa: E402


def olcha(url: str, sekund: int) -> tuple[float, float, int]:
    """(birinchi_keyframe_s, ortacha_oraliq_s, keyframe_soni)."""
    # Kadr turlarini ffprobe paket bayroqlaridan o'qiymiz: dekod
    # qilish shart emas, "K" bayrog'i keyframe degani.
    probe = [ffmpeg_path().replace("ffmpeg", "ffprobe"),
             "-hide_banner", "-rtsp_transport", "tcp",
             "-select_streams", "v:0", "-show_entries",
             "packet=pts_time,flags", "-of", "csv=p=0",
             "-read_intervals", f"%+{sekund}", url]
    try:
        out = subprocess.run(probe, capture_output=True, text=True,
                             timeout=sekund + 40).stdout
    except subprocess.TimeoutExpired:
        return -1.0, -1.0, 0
    vaqtlar = []
    for satr in out.splitlines():
        bolak = satr.strip().split(",")
        if len(bolak) >= 2 and "K" in bolak[1]:
            try:
                vaqtlar.append(float(bolak[0]))
            except ValueError:
                pass
    if not vaqtlar:
        return -1.0, -1.0, 0
    birinchi = vaqtlar[0]
    if len(vaqtlar) < 2:
        return birinchi, -1.0, 1
    farqlar = [b - a for a, b in zip(vaqtlar, vaqtlar[1:])]
    return birinchi, sum(farqlar) / len(farqlar), len(vaqtlar)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("camera_ids", nargs="*", type=int)
    ap.add_argument("--sekund", type=int, default=20)
    ap.add_argument("--hammasi", action="store_true")
    a = ap.parse_args()

    with get_db() as db:
        if a.hammasi:
            rows = db.execute(
                "SELECT * FROM cameras WHERE enabled = 1 AND ip != '' "
                "ORDER BY id").fetchall()
        else:
            q = ",".join("?" * len(a.camera_ids))
            rows = db.execute(
                f"SELECT * FROM cameras WHERE id IN ({q})", a.camera_ids).fetchall()
        rows = [dict(r) for r in rows]
        for r in rows:
            r["_parol"] = security.decrypt(r["password_enc"])

    print("   id ip                birinchi   oraliq  soni  baho")
    for r in rows:
        url = build_rtsp_url(r["ip"], r["port"] or 554, r["rtsp_path"],
                             r["username"] or "", r["_parol"])
        birinchi, oraliq, soni = olcha(url, a.sekund)
        if soni == 0:
            print(f"{r['id']:>5} {r['ip']:<16}        —        —     0  o'qib bo'lmadi")
            continue
        # Ochilish tezligi uchun 1-2 s yaxshi, 4 s dan yuqorisi sezilarli.
        baho = ("yaxshi" if oraliq <= 2 else
                "chidasa bo'ladi" if oraliq <= 4 else "UZUN — sozlash kerak")
        print(f"{r['id']:>5} {r['ip']:<16} {birinchi:>7.2f}s {oraliq:>7.2f}s "
              f"{soni:>5}  {baho}")


if __name__ == "__main__":
    main()

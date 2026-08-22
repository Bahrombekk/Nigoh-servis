"""Kamera RTSP oqimini uzoq muddat tortib ko'radi — "kim aybdor" testi.

MediaMTX jurnalida `[RTSP source] stopped: an error occurred` chiqsa,
savol bitta: ulanishni KAMERA uzdimi yoki MediaMTX'da muammo bormi?
Bu skript MediaMTX'ni chetlab o'tib, kameradan to'g'ridan-to'g'ri
tortadi. Natija ikki xil bo'ladi va ikkalasi ham aniq javob:

  * belgilangan vaqt to'liq o'tdi  -> kamera aybdor emas, muammo bizda;
  * vaqtidan oldin uzildi          -> kamera/registrator uzyapti,
                                      sozlamani o'sha yerda tuzatish kerak.

Parolni terish shart emas — u bazadan olinadi (shifrlangan holda yotadi
va ekranga chiqarilmaydi).

    python scripts/oqim_sinovi.py 13
    python scripts/oqim_sinovi.py 13 --sekund 120 --sub
"""
import argparse
import re
import shutil
import subprocess
import sys
import time

sys.path.insert(0, "/app" if __import__("os").path.isdir("/app/core") else ".")

from core import security                       # noqa: E402
from core.db import get_db                      # noqa: E402
from core.rtsp_probe import build_rtsp_url      # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("camera_id", type=int)
ap.add_argument("--sekund", type=int, default=90)
ap.add_argument("--sub", action="store_true", help="asosiy emas, sub oqim")
ap.add_argument("--yol", default="", help="bazadagisi emas, shu RTSP yo'lni sinash "
                                          "(masalan /LiveMedia/ch1/Media2)")
a = ap.parse_args()

with get_db() as db:
    row = db.execute(
        "SELECT name, ip, port, username, password_enc, rtsp_path, sub_path "
        "FROM cameras WHERE id = ?", (a.camera_id,)).fetchone()
if row is None:
    sys.exit(f"Kamera {a.camera_id} topilmadi")

path = a.yol or (row["sub_path"] if a.sub else row["rtsp_path"]) or ""
if not path:
    sys.exit("Bu kamerada sub yo'l yo'q — --sub o'rniga --yol bilan bering")

url = build_rtsp_url(row["ip"], row["port"] or 554, path,
                     row["username"] or "", security.decrypt(row["password_enc"]))
exe = shutil.which("ffmpeg")
if not exe:
    sys.exit("ffmpeg topilmadi")

print(f"kamera {a.camera_id}  {row['name']}")
print(f"  {row['ip']}:{row['port'] or 554}{path}   ({'sub' if a.sub else 'asosiy'} oqim)")
print(f"  {a.sekund} soniya tortiladi, parol yashirilgan\n")

started = time.monotonic()
proc = subprocess.run(
    [exe, "-hide_banner", "-loglevel", "info", "-rtsp_transport", "tcp",
     "-i", url, "-t", str(a.sekund), "-f", "null", "-"],
    capture_output=True, text=True, timeout=a.sekund + 60)
elapsed = time.monotonic() - started
err = proc.stderr or ""
# Parol xato bilan birga chiqib ketmasin.
err = re.sub(r"rtsp://[^@\s]+@", "rtsp://***@", err)

tail = [ln for ln in err.strip().splitlines() if ln.strip()][-12:]
print("--- ffmpeg chiqishi (oxirgi 12 qator) ---")
for ln in tail:
    print("  " + ln)

print(f"\n--- natija ---")
print(f"  chidadi: {elapsed:.0f} / {a.sekund} soniya   (ffmpeg kodi {proc.returncode})")
if elapsed >= a.sekund - 3 and proc.returncode == 0:
    print("  XULOSA: kamera ulanishni uzmadi. Muammo kamerada EMAS —")
    print("          MediaMTX yoki sozlama tomonida qidirish kerak.")
else:
    print("  XULOSA: ulanish vaqtidan oldin uzildi — aybdor kamera/registrator.")
    print("          Sinab ko'ring: --yol bilan sub oqim (Media2), qurilmada")
    print("          audio (G711) ni o'chirish, RTSP ulanishlar chegarasini")
    print("          oshirish. Nigoh tomonda yumshatish: kamerani")
    print("          \"doim tayyor\" qiling — MediaMTX uzilgan manbani")
    print("          o'zi va darhol qayta ulaydi.")

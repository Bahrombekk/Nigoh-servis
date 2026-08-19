"""Nigoh — Telegram ogohlantirishlari (ixtiyoriy).

TELEGRAM_BOT_TOKEN va TELEGRAM_CHAT_ID muhit o'zgaruvchilari berilsa,
muhim hodisalar (kamera uzildi/qaytdi, oqim muzladi, MediaMTX qayta
ishga tushdi) botga yuboriladi. Berilmasa modul jim turadi — hech qanday
sozlash talab qilinmaydi.

Spamdan saqlanish chaqiruvchi tomonda: health va reconciler bitta
tekshiruvdagi barcha o'zgarishlarni bitta xabarga jamlab yuboradi.
"""
import json
import os
import threading
import urllib.request

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
TIMEOUT = 10.0


def enabled() -> bool:
    return bool(TOKEN and CHAT_ID)


def _send(text: str) -> None:
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"
    data = json.dumps({"chat_id": CHAT_ID, "text": text[:4000]}).encode()
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as res:
            res.read()
    except OSError:
        pass                    # ogohlantirish yetmasa ham tizim ishlayveradi


def send_async(text: str) -> None:
    """Xabarni fonda yuboradi; sozlanmagan bo'lsa hech narsa qilmaydi."""
    if enabled() and text:
        threading.Thread(target=_send, args=(text,), daemon=True).start()

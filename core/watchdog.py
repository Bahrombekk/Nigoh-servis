"""Nigoh — "jarayon tirik, lekin xizmat ko'rsatmayapti" holatidan chiqish.

Nima bo'lgan edi (ishlab chiqarish, o'lchangan). Jarayonga SIGTERM
kelgan, uvicorn "graceful shutdown" ga o'tgan va tinglash soketini
yopgan, lekin OCHIQ SSE ULANISHI (`GET /api/v1/events`, nginx orqali)
hech qachon yopilmagani uchun shu holatda muzlab qolgan:

    INFO:     Shutting down
    INFO:     Waiting for connections to close. (CTRL+C to force quit)

Jarayon tirik (fon vazifalari jurnal yozishda davom etgan), Docker uni
"ishlayapti" deb bilgan, port esa YOPIQ. Oqibati butun xizmatga tarqadi:

    nginx  -> auth_request /_hlsauth -> connection refused -> brauzerga 500
    MediaMTX -> POST /api/auth/stream -> connection refused -> RTSP 401

Ya'ni bitta signal butun video xizmatini muddatsiz o'ldirgan va buni
hech narsa o'z-o'zidan tiklamagan.

Ikki qatlamli himoya qo'yildi:

  1. `main.py` da `timeout_graceful_shutdown` — uvicorn endi cheksiz
     kutmaydi, ulanishlarni majburan uzib chiqib ketadi va Docker'ning
     `restart: unless-stopped` siyosati uni qaytaradi (~5 soniya).

  2. Shu modul — oxirgi chegara. Har CHECK_INTERVAL da xizmatga O'Z
     porti orqali ulanib ko'radi; ketma-ket FAILS_BEFORE_EXIT marta
     ulanib bo'lmasa, jarayon o'zini tugatadi (konteyner qayta
     ko'tariladi). Bu 1-qatlam yopmaydigan har qanday "port o'lgan,
     jarayon tirik" holatini ham qamrab oladi.

Faqat konteynerda ishlaydi: qayta ko'tarishni Docker qiladi. Lokal
ishlab chiqishda jarayonni o'ldirish faqat zarar (qaytaradigan hech kim
yo'q) — shuning uchun u yerda jim turadi. `WATCHDOG=0` — butunlay
o'chirish, `WATCHDOG=1` — konteyner tashqarisida ham majburan yoqish.
"""
import os
import socket
import threading
import time

from .log import log

CHECK_INTERVAL = float(os.environ.get("WATCHDOG_INTERVAL", "30"))
# Ishga tushish uchun muhlat: bootstrap (baza, mediamtx.yml, MediaMTX
# ko'tarilishi) tugamaguncha port hali ochilmagan bo'lishi mumkin.
STARTUP_GRACE = float(os.environ.get("WATCHDOG_GRACE", "90"))
CONNECT_TIMEOUT = 5.0
# Bitta muvaffaqiyatsiz ulanish yetarli emas: fayl deskriptorlari vaqtincha
# tugashi yoki navbat to'lib qolishi mumkin. Uchta ketma-ket (~90 soniya)
# xato — bu endi tasodif emas.
FAILS_BEFORE_EXIT = 3

_started = False
_lock = threading.Lock()


def _in_container() -> bool:
    return os.path.exists("/.dockerenv")


def _enabled() -> bool:
    flag = os.environ.get("WATCHDOG", "")
    if flag == "0":
        return False
    if flag == "1":
        return True
    return _in_container()


def _alive(port: int) -> bool:
    """Xizmat o'z portida ulanish qabul qilyaptimi."""
    try:
        with socket.create_connection(("127.0.0.1", port), CONNECT_TIMEOUT):
            return True
    except OSError:
        return False


def _loop(port: int) -> None:
    time.sleep(STARTUP_GRACE)
    fails = 0
    while True:
        if _alive(port):
            fails = 0
        else:
            fails += 1
            log("watchdog", "port_javob_bermadi", level="warning",
                port=port, urinish=fails, chegara=FAILS_BEFORE_EXIT)
            if fails >= FAILS_BEFORE_EXIT:
                log("watchdog", "qayta_ishga_tushirilmoqda", level="error",
                    port=port,
                    message="Jarayon tirik, lekin port ulanish qabul "
                            "qilmayapti (odatda uvicorn to'xtatish holatida "
                            "qotib qolgan). Jarayon tugatilmoqda — Docker "
                            "uni qaytadan ko'taradi.")
                # os._exit: atexit/graceful yo'llari aynan qotib qolgan
                # bo'lishi mumkin, ya'ni "toza" chiqish ishlamasligi mumkin.
                os._exit(1)
        time.sleep(CHECK_INTERVAL)


def start(port: int) -> bool:
    """Kuzatuvchini fonda ishga tushiradi. Qaytaradi: yoqildimi."""
    global _started
    with _lock:
        if _started or not _enabled():
            return False
        _started = True
    threading.Thread(target=_loop, args=(port,), daemon=True,
                     name="watchdog").start()
    log("watchdog", "started", port=port, interval=CHECK_INTERVAL)
    return True

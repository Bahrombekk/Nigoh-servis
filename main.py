"""Nigoh — kamera servisi (IP kameralarni boshqarish va tarqatish).

Ishga tushirish:
    pip install -r requirements.txt
    set NIGOH_API_KEY=...        # MAJBURIY, usiz servis ko'tarilmaydi
    python main.py

`.env` faylni bu skript O'QIMAYDI — uni faqat `docker compose` yuklaydi
(python-dotenv bog'liqlik sifatida qo'shilmagan). Qo'lda ishga
tushirganda muhit o'zgaruvchilarini o'zingiz bering.

    set PORT=8020        boshqa port
    set ENABLE_UI=1      diagnostika konsolini yoqish (standart: o'chiq)

Admin parolini almashtirish:
    python main.py --admin-parol YangiParol123

Kod tuzilishi:
    main.py            shu fayl — faqat kirish nuqtasi
    api/               BACKEND: config, modellar, endpointlar, analytics
    media/             MEDIAMTX QATLAMI: sync, reconciler, launcher,
                       transport (RTSP transportini o'lchash)
    core/              UMUMIY: db, security, health, snapshots, rtsp_probe,
                       device_info, fast_start, bus, events, metrics, log,
                       watchdog
    debug-ui/          diagnostika konsoli (ENABLE_UI=1 bo'lganda)
    tests/             pytest (pytest.ini: testpaths=tests)
    scripts/           yordamchi skriptlar (qabul_test, import_mediamtx)
    stream_launcher.py MediaMTX chaqiradigan yupqa qobiq (ildizda turishi shart)
"""
import os
import sys

import uvicorn

from api import create_app
from api.bootstrap import bootstrap, change_admin_password
from api.config import API_KEY, PORT
from core import watchdog

# Uvicorn to'xtatish signalini olganda ochiq ulanishlarni shuncha kutadi,
# keyin majburan uzadi.
#
# NIMA UCHUN CHEGARA BOR. Standart holda uvicorn CHEKSIZ kutadi, SSE
# (`/api/v1/events`) ulanishi esa hech qachon o'z-o'zidan yopilmaydi —
# ishlab chiqarishda aynan shu bo'ldi: bitta SSE ulanishi tufayli jarayon
# "Waiting for connections to close" holatida qotib qoldi, tinglash soketi
# yopildi va butun xizmat (HLS auth, RTSP auth, API) muddatsiz o'ldi.
# Tafsiloti: core/watchdog.py.
SHUTDOWN_GRACE = float(os.environ.get("SHUTDOWN_GRACE", "5"))

if "--admin-parol" in sys.argv:
    index_of = sys.argv.index("--admin-parol")
    if index_of + 1 >= len(sys.argv):
        sys.exit("Parolni ko'rsating:  python main.py --admin-parol YangiParol")
    new_password = sys.argv[index_of + 1]
    if len(new_password) < 6:
        sys.exit("Parol kamida 6 belgidan iborat bo'lsin")
    change_admin_password(new_password)
    sys.exit(0)

# Yagona kirish X-API-Key — kalitsiz servis himoyasiz qolardi, shuning
# uchun ishga tushmaydi (fail fast). Kalit hosil qilish: openssl rand -hex 32
if not API_KEY:
    sys.exit("NIGOH_API_KEY o'rnatilmagan — .env ga uzun tasodifiy kalit "
             "yozing (masalan: openssl rand -hex 32) va qayta ishga tushiring.")

bootstrap()
app = create_app()

if __name__ == "__main__":
    # 8000-port ko'pincha band bo'ladi (Docker Desktop, Windows xizmatlari) —
    # boshqa portni PORT muhit o'zgaruvchisi orqali berish mumkin.
    #
    # Avtomatik qayta yuklash faqat kod yozayotganda kerak (set RELOAD=1);
    # Windows'da u ba'zan osilib qoladi, shuning uchun standart holatda o'chiq.
    if os.environ.get("RELOAD") == "1":
        # reload rejimida uvicorn modulni o'zi qayta import qiladi — satr beriladi.
        uvicorn.run("main:app", host="0.0.0.0", port=PORT, reload=True)
    else:
        # Tayyor obyekt beriladi — modul qayta import qilinmaydi,
        # bootstrap ham ikki marta ishlamaydi.
        # Port o'lib, jarayon tirik qolgan holat uchun oxirgi chegara
        # (konteynerda o'zini tugatadi, Docker qaytaradi).
        watchdog.start(PORT)
        uvicorn.run(app, host="0.0.0.0", port=PORT,
                    timeout_graceful_shutdown=SHUTDOWN_GRACE)

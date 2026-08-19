"""Nigoh — kamera xaritasi va super-admin paneli.

Ishga tushirish:
    pip install -r requirements.txt
    python main.py
Keyin brauzerda:  http://localhost:8010
(Portni o'zgartirish:  set PORT=8020  &&  python main.py)

Admin parolini almashtirish:
    python main.py --admin-parol YangiParol123

Kod tuzilishi:
    main.py            shu fayl — faqat kirish nuqtasi
    api/               BACKEND: config, modellar, endpointlar
    media/             MEDIAMTX QATLAMI: sync (konfiguratsiya/API), launcher
    core/              UMUMIY: db, security, health, rtsp_probe, fast_start,
                       snapshots, device_info, bus
    debug-ui/          diagnostika sahifasi (ENABLE_UI=1 bo'lganda)
    scripts/           yordamchi skriptlar (qabul_test, import_mediamtx)
    stream_launcher.py MediaMTX chaqiradigan yupqa qobiq (ildizda turishi shart)
"""
import os
import sys

import uvicorn

from api import create_app
from api.bootstrap import bootstrap, change_admin_password
from api.config import API_KEY, PORT

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
        uvicorn.run(app, host="0.0.0.0", port=PORT)

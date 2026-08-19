"""Nigoh — umumiy infratuzilma. Buni ham backend (app/), ham MediaMTX
qatlami (media/) ishlatadi.

    core/db.py          SQLite sxemasi va migratsiya
    core/security.py    parollar shifri (Fernet), admin paroli, sessiyalar
    core/health.py      kameralar tirikligini fonda kuzatish
    core/rtsp_probe.py  RTSP tekshiruv: tarmoq, login, kodek
    core/fast_start.py  surat (snapshot) va keyframe so'rash

Ma'lumot fayllari (cameras.db, secret.key) loyiha ildizida qoladi —
paket ko'chsa ham yo'llar o'zgarmaydi.
"""

"""Nigoh — dashboard uchun tarixiy statistika.

health.py har daqiqada kameralarni tekshiradi, ammo natija faqat xotirada
turadi — server qayta ishga tushsa tarix yo'qoladi. Bu modul o'sha
tekshiruv natijalarini bazaga yozib boradi:

  * stats_region — har 5 daqiqada hudud kesimida nechta kamera onlayn edi
  * stats_event  — kamera uzildi/qayta ulandi hodisalari (aniq vaqti bilan)

Shu ikkovidan dashboard 24 soatlik grafik, 7 kunlik kunlik ko'rsatkichlar
va hudud kesimidagi statistikani chiqaradi. Hajm nazorati: 12 hudud bilan
sutkada ~3,5 ming qator, 30 kundan eskisi o'chirib boriladi.
"""
import threading
import time
from datetime import datetime, timedelta, timezone

from . import alerts
from .db import get_db

SNAPSHOT_INTERVAL = 300.0   # soniya — 5 daqiqa: sutkada 288 nuqta yetarli
KEEP_DAYS = 30              # tarix shundan eski bo'lsa o'chiriladi

_prev: dict[int, bool] = {}    # kamera id → oxirgi ma'lum holat
_last_snapshot = 0.0
_lock = threading.Lock()


def record_sweep(statuses: dict[tuple[str, int], bool]) -> None:
    """health._sweep natijasini tarixga yozadi (har daqiqa chaqiriladi).

    IP'siz (tayyor oqim) kameralarning tirikligi o'lchanmaydi, shuning
    uchun ular statistikaga kirmaydi — foizlar faqat kuzatiladigan
    kameralar ustidan hisoblanadi.
    """
    global _last_snapshot
    now = datetime.now(timezone.utc)
    now_iso = now.isoformat()

    with get_db() as db:
        cams = db.execute(
            "SELECT id, name, region, ip, port FROM cameras "
            "WHERE enabled = 1 AND ip IS NOT NULL AND ip != ''"
        ).fetchall()

        # 1) Holat o'zgarishlari — hodisa sifatida (aniq vaqti bilan).
        events: list[tuple] = []
        with _lock:
            ids = {cam["id"] for cam in cams}
            for cam_id in list(_prev):
                if cam_id not in ids:
                    _prev.pop(cam_id)
            for cam in cams:
                online = statuses.get((cam["ip"], cam["port"] or 554))
                if online is None:
                    continue
                prev = _prev.get(cam["id"])
                if prev is not None and prev != online:
                    events.append((now_iso, cam["id"], cam["name"],
                                   cam["region"],
                                   "online" if online else "offline"))
                _prev[cam["id"]] = online
        if events:
            db.executemany(
                "INSERT INTO stats_event (ts, camera_id, name, region, kind) "
                "VALUES (?, ?, ?, ?, ?)", events)
            # Telegram sozlangan bo'lsa (TELEGRAM_BOT_TOKEN/CHAT_ID) —
            # bitta sweep'dagi barcha o'zgarishlar bitta xabarda ketadi.
            alerts.send_async("\n".join(
                f"{'🟢 qaytdi' if kind == 'online' else '🔴 uzildi'}: "
                f"{name} ({region})"
                for _, _, name, region, kind in events))

        # 2) Hudud kesimidagi surat — har 5 daqiqada bitta.
        if time.time() - _last_snapshot < SNAPSHOT_INTERVAL:
            return
        _last_snapshot = time.time()

        by_region: dict[str, list[bool]] = {}
        for cam in cams:
            online = statuses.get((cam["ip"], cam["port"] or 554))
            if online is None:
                continue
            by_region.setdefault(cam["region"], []).append(online)
        if by_region:
            db.executemany(
                "INSERT INTO stats_region (ts, region, total, online) "
                "VALUES (?, ?, ?, ?)",
                [(now_iso, region, len(v), sum(v))
                 for region, v in by_region.items()])

        cutoff = (now - timedelta(days=KEEP_DAYS)).isoformat()
        db.execute("DELETE FROM stats_region WHERE ts < ?", (cutoff,))
        db.execute("DELETE FROM stats_event WHERE ts < ?", (cutoff,))

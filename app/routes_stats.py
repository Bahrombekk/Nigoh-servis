"""Nigoh — dashboard statistikasi (kirishsiz, xarita kabi ochiq).

Ma'lumot manbai — core/stats.py yozib boradigan ikki jadval:
stats_region (5 daqiqalik hudud suratlari) va stats_event (uzilishlar).
Hammasi bitta endpointda — dashboard bitta so'rov bilan chizadi.
"""
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter

from core.db import get_db

# Prefiks nisbiy — create_app uni /api/v1 (asosiy) va /api (eski) ostida ulaydi.
router = APIRouter(prefix="/stats", tags=["stats"])


@router.get("/dashboard")
def dashboard_stats():
    now = datetime.now(timezone.utc)
    iso24 = (now - timedelta(hours=24)).isoformat()
    iso7 = (now - timedelta(days=8)).isoformat()   # 7 kunlik oynaga zaxira bilan

    with get_db() as db:
        # 24 soatlik chiziq: har bir surat vaqtida jami nechta onlayn edi.
        timeline = [
            {"ts": r["ts"], "online": r["online"], "total": r["total"]}
            for r in db.execute(
                "SELECT ts, SUM(online) AS online, SUM(total) AS total "
                "FROM stats_region WHERE ts >= ? GROUP BY ts ORDER BY ts",
                (iso24,))
        ]

        # Kunlik kesim (mahalliy sana bo'yicha): o'rtacha onlayn ulushi
        # va uzilish hodisalari soni.
        daily_up = {
            r["d"]: (r["online"], r["total"])
            for r in db.execute(
                "SELECT date(ts, 'localtime') AS d, SUM(online) AS online, "
                "SUM(total) AS total FROM stats_region WHERE ts >= ? GROUP BY d",
                (iso7,))
        }
        daily_ev = {
            r["d"]: r["n"]
            for r in db.execute(
                "SELECT date(ts, 'localtime') AS d, COUNT(*) AS n "
                "FROM stats_event WHERE kind = 'offline' AND ts >= ? GROUP BY d",
                (iso7,))
        }
        today = date.today()
        daily = []
        for i in range(6, -1, -1):
            d = (today - timedelta(days=i)).isoformat()
            online, total = daily_up.get(d, (0, 0))
            daily.append({
                "date": d,
                "uptime": round(100 * online / total, 1) if total else None,
                "events": daily_ev.get(d, 0),
            })

        # Hudud kesimi: 24 soatlik o'rtacha onlayn ulushi va bugungi uzilishlar.
        reg_ev = {
            r["region"]: r["n"]
            for r in db.execute(
                "SELECT region, COUNT(*) AS n FROM stats_event "
                "WHERE kind = 'offline' "
                "AND date(ts, 'localtime') = date('now', 'localtime') "
                "GROUP BY region")
        }
        regions = [
            {
                "region": r["region"],
                "uptime24": round(100 * r["online"] / r["total"], 1)
                            if r["total"] else None,
                "events_today": reg_ev.get(r["region"], 0),
            }
            for r in db.execute(
                "SELECT region, SUM(online) AS online, SUM(total) AS total "
                "FROM stats_region WHERE ts >= ? GROUP BY region", (iso24,))
        ]

        # Bugungi uzilishlar soat kesimida — 24 katakli ustuncha uchun.
        hourly = [0] * 24
        for r in db.execute(
                "SELECT CAST(strftime('%H', ts, 'localtime') AS INTEGER) AS h, "
                "COUNT(*) AS n FROM stats_event WHERE kind = 'offline' "
                "AND date(ts, 'localtime') = date('now', 'localtime') GROUP BY h"):
            if 0 <= r["h"] <= 23:
                hourly[r["h"]] = r["n"]

        # So'nggi hodisalar — sahifa yangilansa ham yo'qolmaydigan lenta.
        events = [
            {"ts": r["ts"], "name": r["name"], "region": r["region"],
             "kind": r["kind"]}
            for r in db.execute(
                "SELECT ts, name, region, kind FROM stats_event "
                "ORDER BY id DESC LIMIT 40")
        ]

    return {
        "timeline": timeline,
        "daily": daily,
        "regions": regions,
        "hourly_today": hourly,
        "events_today": sum(hourly),
        "events": events,
    }

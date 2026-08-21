"""Nigoh — uzilishlar tahlili: uptime, guruhlar reytingi, soatlik profil.

Manba — `events` jadvalidagi `online`/`offline` o'tishlari (saqlash muddati
30 kun). Ular allaqachon yozilardi, lekin faqat bitta kamera kesimida
o'qilardi (`/admin/cameras/{ref}/uptime`). Bu yerda ikki savolga javob
beradigan agregatlar bor:

  * **kim aybdor** — `/admin/uptime?group_by=...`: qaysi hudud, qaysi
    registrator yoki qaysi tugun eng ko'p uzilish beryapti. 5000 kamerani
    ko'z bilan ko'rib bo'lmaydi, guruh reytingi esa darhol yo'naltiradi.
  * **qachon buzilyapti** — `/admin/outages/hourly`: uzilishlar sutka
    bo'ylab qanday taqsimlangan. Cho'qqi ish soatlariga tushsa sabab
    odatda yuk yoki tarmoq, kechasi tushsa — jadval bo'yicha qayta
    yuklanish yoki texnik xizmat.

Nima uchun alohida modul: `admin.py` allaqachon 800 qatordan oshgan, bu
esa mustaqil mavzu.
"""
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query

from core import health
from core.db import get_db

from .helpers import require_admin, resolve_ref

# Prefiks /admin bo'lib qoladi — mavjud mijozlar bitta prefiks bilan ishlasin.
router = APIRouter(prefix="/admin", tags=["analytics"],
                   dependencies=[Depends(require_admin)])

# Hodisalar 30 kun saqlanadi — undan uzoq oraliq so'ralsa javob yolg'on
# bo'lardi (ma'lumot yo'qligi "uzilish bo'lmagan" bo'lib ko'rinadi).
MAX_HOURS = 24 * 30

GROUP_KEYS = ("region", "nvr", "node")


def _parse_ts(ts: str) -> datetime:
    """SQLite `datetime('now')` — UTC, lekin zonasiz satr."""
    return datetime.fromisoformat(ts).replace(tzinfo=timezone.utc)


def _sqlite_since(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%d %H:%M:%S")


def _offline_seconds(transitions: list[tuple[datetime, str]],
                     since: datetime, now: datetime,
                     online_now: bool | None) -> float:
    """O'tishlar ro'yxatidan davr ichidagi offline sekundlarni hisoblaydi.

    Davr boshidagi holat — birinchi o'tishning teskarisi (agar birinchi
    hodisa "online" bo'lsa, undan oldin kamera offline turgan). O'tish
    umuman bo'lmasa hozirgi holat butun davrga taalluqli deb olinadi.
    """
    if transitions:
        state = "offline" if transitions[0][1] == "online" else "online"
    else:
        state = "offline" if online_now is False else "online"

    cursor, offline = since, 0.0
    for moment, kind in transitions:
        if moment > cursor and state == "offline":
            offline += (moment - cursor).total_seconds()
        cursor, state = moment, kind
    if state == "offline" and now > cursor:
        offline += (now - cursor).total_seconds()
    return offline


def _transitions_by_slug(db, since: datetime) -> dict[str, list]:
    """Davr ichidagi barcha online/offline o'tishlari, slug bo'yicha.

    Bitta so'rov — 5000 kamera uchun ham. Kamera kesimida alohida
    so'rov yuborilsa 5000 ta so'rov bo'lardi.
    """
    rows = db.execute(
        "SELECT slug, ts, kind FROM events "
        "WHERE kind IN ('online', 'offline') AND ts >= ? AND slug IS NOT NULL "
        "ORDER BY slug, ts, id",
        (_sqlite_since(since),),
    ).fetchall()
    grouped: dict[str, list] = {}
    for row in rows:
        grouped.setdefault(row["slug"], []).append((_parse_ts(row["ts"]),
                                                    row["kind"]))
    return grouped


def _camera_state(row, online_now: bool | None) -> str:
    """Sodda holat — bu yerda `stalled` kerak emas (u jonli ko'rsatkich,
    tarixiy emas), shuning uchun reconciler'ga murojaat qilinmaydi."""
    if not row["enabled"]:
        return "disabled"
    if not row["ip"]:
        return "unknown"
    if online_now is None:
        return "unknown"
    return "online" if online_now else "offline"


def _collect(hours: int):
    """Har kamera uchun (qator, holat, uzilishlar, offline sekundlar)."""
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)
    with get_db() as db:
        rows = db.execute(
            "SELECT id, external_id, name, region, slug, ip, port, node_id, "
            "enabled FROM cameras"
        ).fetchall()
        by_slug = _transitions_by_slug(db, since)
        nodes = {r["id"]: r["name"] for r in db.execute("SELECT id, name FROM nodes")}

    out = []
    for row in rows:
        transitions = by_slug.get(row["slug"] or "", [])
        online_now = health.online(row["ip"], row["port"])
        offline = _offline_seconds(transitions, since, now, online_now)
        outages = sum(1 for _, kind in transitions if kind == "offline")
        out.append({
            "row": row,
            "state": _camera_state(row, online_now),
            "outages": outages,
            "offline_seconds": int(offline),
            "last_offline_at": next(
                (moment.isoformat(timespec="seconds")
                 for moment, kind in reversed(transitions) if kind == "offline"),
                None),
        })
    total_seconds = max(1, int((now - since).total_seconds()))
    return out, total_seconds, since, now, nodes


def _uptime_pct(offline_seconds: int, total_seconds: int) -> float:
    return round(100 * (total_seconds - offline_seconds) / total_seconds, 2)


@router.get("/uptime")
def fleet_uptime(hours: int = Query(default=24, ge=1, le=MAX_HOURS),
                 group_by: str = "", limit: int = Query(default=100, ge=1,
                                                        le=5000)):
    """Uzilishlar bo'yicha reyting — eng yomoni birinchi.

    `group_by` berilmasa: kamera kesimida, `limit` tagacha (uzilishlar
    soni bo'yicha kamayish tartibida). Butun ro'yxat kerak bo'lsa
    `limit` ni oshiring — 5000 kamerada javob ~600 KB bo'ladi.

    `group_by=region|nvr|node`: guruh kesimida agregat. Guruhlar soni
    kameralar sonidan ancha kam, shuning uchun javob kichik va aynan
    shu ko'rinish "qayerni tuzatish kerak" degan savolga javob beradi.
    `nvr` — registrator manzili (bitta IP ortida o'nlab kanal turadi),
    `region` bo'sh kameralar `belgilanmagan` guruhiga tushadi.
    """
    if group_by and group_by not in GROUP_KEYS:
        raise HTTPException(400, f"group_by faqat: {', '.join(GROUP_KEYS)}")

    collected, total_seconds, since, now, nodes = _collect(hours)
    window = {"hours": hours, "from": since.isoformat(timespec="seconds"),
              "to": now.isoformat(timespec="seconds")}

    if not group_by:
        cameras = sorted(
            collected,
            key=lambda item: (-item["outages"], -item["offline_seconds"]))[:limit]
        return {**window, "total": len(collected), "shown": len(cameras),
                "cameras": [{
                    "id": item["row"]["id"],
                    "external_id": item["row"]["external_id"] or "",
                    "name": item["row"]["name"],
                    "region": item["row"]["region"] or "",
                    "state": item["state"],
                    "outages": item["outages"],
                    "offline_seconds": item["offline_seconds"],
                    "uptime_pct": _uptime_pct(item["offline_seconds"], total_seconds),
                    "last_offline_at": item["last_offline_at"],
                } for item in cameras]}

    def key_of(row) -> str:
        if group_by == "region":
            return (row["region"] or "").strip() or "belgilanmagan"
        if group_by == "nvr":
            return row["ip"] or "manzilsiz"
        return nodes.get(row["node_id"] or 1, f"tugun-{row['node_id'] or 1}")

    groups: dict[str, dict] = {}
    for item in collected:
        bucket = groups.setdefault(key_of(item["row"]), {
            "key": "", "cameras": 0, "online": 0, "offline": 0,
            "unknown": 0, "disabled": 0, "outages": 0, "offline_seconds": 0})
        bucket["key"] = key_of(item["row"])
        bucket["cameras"] += 1
        bucket[item["state"]] += 1
        bucket["outages"] += item["outages"]
        bucket["offline_seconds"] += item["offline_seconds"]

    ranked = []
    for bucket in groups.values():
        # Guruh uptime'i — a'zolarining o'rtachasi (kamera-soat bo'yicha).
        span = max(1, bucket["cameras"] * total_seconds)
        bucket["uptime_pct"] = round(
            100 * (span - bucket["offline_seconds"]) / span, 2)
        ranked.append(bucket)
    ranked.sort(key=lambda b: (-b["outages"], -b["offline_seconds"]))
    return {**window, "group_by": group_by, "groups": ranked}


@router.get("/outages/hourly")
def outages_hourly(hours: int = Query(default=24, ge=1, le=MAX_HOURS),
                   ref: str = "",
                   tz_offset_minutes: int = Query(default=0, ge=-840, le=840)):
    """Uzilishlarning sutka bo'ylab taqsimoti — 24 ta ustun.

    `ref` berilsa bitta kamera, aks holda butun park. `tz_offset_minutes`
    — mijozning zonasi (`-new Date().getTimezoneOffset()`): hodisalar
    bazada UTC'da yotadi, lekin "cho'qqi 08:00 da" degan xulosa faqat
    mahalliy vaqtda ma'noga ega. Standart 0 = UTC.

    `peak` — eng zich uch soatlik oyna: texnik xizmatni shu oynadan
    tashqariga rejalashtirish kerak.
    """
    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=hours)

    sql = ("SELECT ts FROM events WHERE kind = 'offline' AND ts >= ?")
    params: list = [_sqlite_since(since)]
    slug = ""
    if ref:
        with get_db() as db:
            row = resolve_ref(db, ref)
        if row is None:
            raise HTTPException(404, "Kamera topilmadi")
        slug = row["slug"] or ""
        sql += " AND slug = ?"
        params.append(slug)

    with get_db() as db:
        rows = db.execute(sql, params).fetchall()

    buckets = [0] * 24
    shift = timedelta(minutes=tz_offset_minutes)
    for row in rows:
        buckets[(_parse_ts(row["ts"]) + shift).hour] += 1

    # Eng zich uch soatlik oyna — sutka aylanasi bo'ylab (23→00 ham).
    best_start, best_sum = 0, -1
    for start in range(24):
        window_sum = sum(buckets[(start + i) % 24] for i in range(3))
        if window_sum > best_sum:
            best_start, best_sum = start, window_sum

    return {
        "hours": hours,
        "from": since.isoformat(timespec="seconds"),
        "to": now.isoformat(timespec="seconds"),
        "tz_offset_minutes": tz_offset_minutes,
        "ref": ref or "",
        "total": len(rows),
        "hourly": buckets,
        "peak": {"from_hour": best_start, "to_hour": (best_start + 3) % 24,
                 "outages": max(0, best_sum)},
    }

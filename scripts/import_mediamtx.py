"""Mavjud mediamtx.yml dagi kameralarni bazaga ko'chirish.

Qo'lda yozilgan `paths` yozuvlarini o'qiydi, RTSP manzilni bo'laklarga
ajratadi (IP, port, login, parol, yo'l) va bazaga yozadi. Parol shifrlanadi.

Ishlatish:
    python import_mediamtx.py            # nima bo'lishini ko'rsatadi
    python import_mediamtx.py --yoz      # bazaga haqiqatan yozadi
"""
import sys
import urllib.parse
from pathlib import Path

import yaml

# Skript scripts/ ichidan ishga tushirilganda ham loyiha modullarini topsin.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core import security                                        # noqa: E402
from core.db import get_db, init_db, slugify, unique_slug        # noqa: E402

BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "mediamtx.yml"

# Yangi kamera yaratilsa shu markazlardan koordinata olinadi.
REGION_CENTERS = {
    "toshkent": (41.3111, 69.2797),
    "samarqand": (39.6547, 66.9758),
    "buxoro": (39.7747, 64.4197),
    "xorazm": (41.5500, 60.6333),
    "andijon": (40.7821, 72.3442),
    "namangan": (40.9983, 71.6726),
    "farg_ona": (40.3864, 71.7864),
    "qashqadaryo": (38.8610, 65.7847),
    "surxondaryo": (37.2242, 67.2783),
    "jizzax": (40.1158, 67.8422),
    "sirdaryo": (40.4897, 68.7842),
    "navoiy": (40.1039, 65.3733),
}


def parse_source(source: str) -> dict | None:
    """rtsp://login:parol@ip:port/yo'l  ->  bo'laklar."""
    if not source.startswith("rtsp://"):
        return None
    parts = urllib.parse.urlsplit(source)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    return {
        "ip": parts.hostname or "",
        "port": parts.port or 554,
        "username": urllib.parse.unquote(parts.username or ""),
        "password": urllib.parse.unquote(parts.password or ""),
        "rtsp_path": path,
    }


def guess_vendor(path: str) -> str:
    lowered = path.lower()
    if "streaming/channels" in lowered:
        return "hikvision"
    if "realmonitor" in lowered:
        return "dahua"
    if "axis-media" in lowered:
        return "axis"
    if "h264preview" in lowered:
        return "reolink"
    if "media/video" in lowered:
        return "uniview"
    return "boshqa"


def find_match(db, path_name: str):
    """yml dagi path nomiga mos bazadagi kamerani topadi."""
    rows = db.execute("SELECT id, name, region, slug FROM cameras").fetchall()
    for row in rows:                                  # aniq moslik
        if row["slug"] == path_name:
            return row
    for row in rows:                                  # prefiks bo'yicha
        slug = row["slug"] or ""
        if slug.startswith(path_name) or path_name.startswith(slug):
            return row
    return None


def region_from(path_name: str) -> tuple[str, float, float]:
    head = path_name.split("_")[0]
    lat, lng = REGION_CENTERS.get(head, (41.35, 64.6))
    return head.capitalize(), lat, lng


def main() -> None:
    apply = "--yoz" in sys.argv

    if not CONFIG_PATH.exists():
        sys.exit("mediamtx.yml topilmadi")

    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8")) or {}
    paths = config.get("paths") or {}
    if not paths:
        sys.exit("mediamtx.yml ichida `paths` bo'limi bo'sh")

    init_db()
    plan = []

    with get_db() as db:
        for path_name, conf in paths.items():
            source = (conf or {}).get("source", "")
            parsed = parse_source(source)
            if parsed is None:
                plan.append(("o'tkazib yuborildi", path_name,
                             "RTSP manzil emas: " + str(source)[:60]))
                continue

            match = find_match(db, path_name)
            vendor = guess_vendor(parsed["rtsp_path"])

            if match:
                plan.append(("yangilanadi", path_name,
                             f'"{match["name"]}" ({match["region"]}) yozuviga '
                             f'{parsed["ip"]}:{parsed["port"]} biriktiriladi'))
                if apply:
                    db.execute(
                        "UPDATE cameras SET slug=?, ip=?, port=?, username=?, "
                        "password_enc=?, rtsp_path=?, vendor=?, stream_url='', "
                        "enabled=1 WHERE id=?",
                        (path_name, parsed["ip"], parsed["port"], parsed["username"],
                         security.encrypt(parsed["password"]) if parsed["password"] else "",
                         parsed["rtsp_path"], vendor, match["id"]),
                    )
            else:
                region, lat, lng = region_from(path_name)
                name = path_name.replace("_", " ").title()
                plan.append(("yangi qo'shiladi", path_name,
                             f'"{name}" ({region}) — koordinata taxminiy, '
                             f"keyin xaritadan aniqlang"))
                if apply:
                    slug = unique_slug(db, path_name) if find_match(db, path_name) else path_name
                    db.execute(
                        "INSERT INTO cameras (name, region, lat, lng, stream_url, slug, "
                        "ip, port, username, password_enc, rtsp_path, vendor, enabled, note) "
                        "VALUES (?, ?, ?, ?, '', ?, ?, ?, ?, ?, ?, ?, 1, ?)",
                        (name, region, lat, lng, slug, parsed["ip"], parsed["port"],
                         parsed["username"],
                         security.encrypt(parsed["password"]) if parsed["password"] else "",
                         parsed["rtsp_path"], vendor,
                         "mediamtx.yml dan import qilindi — joyini aniqlang"),
                    )

    width = max(len(a) for a, _, _ in plan)
    print()
    for action, name, detail in plan:
        print(f"  {action.ljust(width)}  {name}")
        print(f"  {' ' * width}  {detail}")
    print()

    if apply:
        print("  Bazaga yozildi. Parollar shifrlangan holda saqlandi.")
        print("  Endi saytdagi boshqaruv panelidan kameralarni ko'rasiz.\n")
    else:
        print("  Bu faqat ko'rsatuv edi. Haqiqatan yozish uchun:")
        print("      python import_mediamtx.py --yoz\n")


if __name__ == "__main__":
    main()

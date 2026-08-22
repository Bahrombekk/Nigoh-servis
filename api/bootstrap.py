"""Nigoh — birinchi ishga tushirish tayyorgarligi."""
import os

from core import health, security, snapshots
from core.db import get_db, init_db
from core.log import log
from media import reconciler
from media import sync as mediamtx_sync

from .helpers import cameras_for_mediamtx


def _load_cameras() -> list[dict]:
    """Reconciler uchun: kameralarning MediaMTX ko'rinishi, har safar bazadan."""
    with get_db() as db:
        return cameras_for_mediamtx(db)


def _streaming_pairs() -> set[tuple[str, int]]:
    """Ayni damda MediaMTX oqim olayotgan kameralarning (ip, port) to'plami.

    Health tekshiruvi shu ro'yxatga qaraydi: TCP javob bermasa ham,
    MediaMTX kameradan bayt olayotgan bo'lsa kamera tirik hisoblanadi.
    Sweep'da faqat tekshiruvdan o'tmagan manzil bo'lsa chaqiriladi.

    core/ media/ ga bog'lanmasligi uchun funksiya shu qatlamda turadi va
    health'ga uzatiladi (reconciler'dagi `load_cameras` bilan bir xil).
    """
    paths = mediamtx_sync.list_active_paths()
    if not paths:
        return set()
    ready = set()
    for name, item in paths.items():
        if not item.get("ready"):
            continue
        base = name
        for suffix in (mediamtx_sync.TRANSCODE_SUFFIX, mediamtx_sync.SUB_SUFFIX):
            if base.endswith(suffix):
                base = base[: -len(suffix)]
        ready.add(base)
    if not ready:
        return set()
    with get_db() as db:
        rows = db.execute(
            "SELECT ip, port FROM cameras WHERE slug IN "
            f"({','.join('?' * len(ready))})", tuple(ready)).fetchall()
    return {(r["ip"], r["port"] or 554) for r in rows if r["ip"]}


def bootstrap() -> None:
    init_db()

    # Kameralarning tirikligini fonda kuzatib boramiz — xaritada o'chiq
    # kameralar qizil bo'lib ko'rinadi.
    # Oqim ketayotgan kamera "o'chiq" deb belgilanmasin: TCP tekshiruvi
    # qurilma band yoki sekin bo'lganda ham yiqiladi, MediaMTX'dagi bayt
    # esa tiriklikning aniq dalili.
    health.set_streaming_probe(_streaming_pairs)
    health.start()

    # Suratlar diskda, pog'onali yangilanadi: issiq (so'ralgan) — 10 s,
    # sovuq online — 5 daqiqa. Poster'lar shu zaxiradan darhol beriladi.
    snapshots.start()

    # mediamtx.yml har ishga tushishda qayta yoziladi: portlar va kirish
    # nazorati sozlamalari kod bilan birga yangilansin. MediaMTX ishlab
    # turgan bo'lsa faylni o'zi qayta o'qiydi — qo'lda hech narsa kerak emas.
    with get_db() as db:
        mediamtx_sync.write_config(cameras_for_mediamtx(db))

    # MediaMTX'ni fonda kuzatib turamiz: yiqilsa qayta ishga tushiriladi,
    # yo'llar (kamera qo'shildi/o'chirildi, MediaMTX qayta ko'tarildi)
    # o'z-o'zidan kelishtiriladi. Sayt ochilishini kutdirmaydi.
    reconciler.start(_load_cameras)

    log("app", "started")

    with get_db() as db:
        generated = security.ensure_admin(db)
    if generated:
        log("app", "admin_created",
            username=os.environ.get("ADMIN_LOGIN", "admin"))
        login_name = os.environ.get("ADMIN_LOGIN", "admin")
        print("\n" + "=" * 58)
        print("  SUPER-ADMIN YARATILDI — bu ma'lumotni saqlab qo'ying")
        print(f"     login:  {login_name}")
        print(f"     parol:  {generated}")
        print("  Parolni almashtirish:")
        print("     python main.py --admin-parol YangiParol")
        print("=" * 58 + "\n")


def change_admin_password(new_password: str) -> None:
    """`python main.py --admin-parol Yangi` buyrug'i uchun."""
    init_db()
    with get_db() as conn:
        security.set_password(conn, os.environ.get("ADMIN_LOGIN", "admin"), new_password)
    print("Parol almashtirildi. Barcha eski sessiyalar bekor qilindi.")

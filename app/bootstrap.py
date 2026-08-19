"""Nigoh — birinchi ishga tushirish tayyorgarligi."""
import os

from core import health, security
from core.db import get_db, init_db
from core.log import log
from media import reconciler
from media import sync as mediamtx_sync

from .helpers import cameras_for_mediamtx


def _load_cameras() -> list[dict]:
    """Reconciler uchun: kameralarning MediaMTX ko'rinishi, har safar bazadan."""
    with get_db() as db:
        return cameras_for_mediamtx(db)


def bootstrap() -> None:
    init_db()

    # Kameralarning tirikligini fonda kuzatib boramiz — xaritada o'chiq
    # kameralar qizil bo'lib ko'rinadi.
    health.start()

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

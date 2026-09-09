"""Devor (mozaika) registri.

`media/walls.py` va `media/mosaic.py` testsiz kirgan edi — shu bo'shliq
darhol o'zini ko'rsatdi: yangi `~^wall_...$` shabloni `desired_paths`
testini jimgina buzdi. Bu yerda registrning o'zi qulflanadi.
"""
from core.db import get_db
from media import walls
from media.mosaic import grid_for


def _tozala():
    walls.ensure_table()
    with get_db() as db:
        db.execute("DELETE FROM walls")


# ---------- kalit ----------

def test_bir_xil_tanlov_bir_xil_kalit():
    """Ikki operator bir xil devor so'rasa bitta oqimni bo'lishadi —
    server ikki marta kodlamaydi."""
    assert walls.wall_key([1, 2, 3], 2, 2) == walls.wall_key([1, 2, 3], 2, 2)


def test_tartib_kalitni_ozgartiradi():
    """Tartib katak joylashuvini belgilaydi — boshqa tartib boshqa devor."""
    assert walls.wall_key([1, 2, 3], 2, 2) != walls.wall_key([3, 2, 1], 2, 2)


def test_setka_kalitni_ozgartiradi():
    assert walls.wall_key([1, 2, 3, 4], 2, 2) != walls.wall_key([1, 2, 3, 4], 4, 1)


# ---------- saqlash ----------

def test_saqlangan_devor_qaytib_oqiladi():
    _tozala()
    key = walls.save_wall([5, 7, 9], 3, 1)
    w = walls.load_wall(key)
    assert w == {"camera_ids": [5, 7, 9], "cols": 3, "rows": 1}


def test_yoq_kalit_none():
    _tozala()
    assert walls.load_wall("yoqbunday") is None


def test_registr_cheksiz_osmaydi(monkeypatch):
    """Har xil tanlov alohida qator qoldiradi; chegara bo'lmasa jadval
    cheksiz o'sardi — o'chirish ham, TTL ham yo'q edi."""
    _tozala()
    monkeypatch.setattr(walls, "WALL_LIMIT", 10)
    for i in range(25):
        walls.save_wall([i, i + 1], 2, 1)
    with get_db() as db:
        soni = db.execute("SELECT COUNT(*) FROM walls").fetchone()[0]
    assert soni <= 10


def test_ishlatilayotgan_devor_ochirilmaydi(monkeypatch):
    """Devor har ochilishida qayta yoziladi — eng eski bo'lib qolmaydi."""
    _tozala()
    monkeypatch.setattr(walls, "WALL_LIMIT", 5)
    key = walls.save_wall([100, 101], 2, 1)
    for i in range(10):                      # boshqalar registrni to'ldiradi
        walls.save_wall([i, i + 1], 2, 1)
        walls.save_wall([100, 101], 2, 1)    # bu devor ishlatilyapti
    assert walls.load_wall(key) is not None


# ---------- setka tanlash ----------

def test_grid_for_kvadratga_yaqin():
    assert grid_for(4, None, None) == (2, 2)
    assert grid_for(9, None, None) == (3, 3)


def test_grid_for_berilgan_olcham_saqlanadi():
    assert grid_for(4, 4, 1) == (4, 1)

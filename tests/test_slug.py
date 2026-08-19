from core.db import get_db, slugify, unique_slug


def test_slugify_lotin_va_kirill():
    assert slugify("Toshkent Amir Temur") == "toshkent_amir_temur"
    assert slugify("Farg'ona ko'chasi") == "fargona_kochasi"
    assert slugify("Чорсу бозори") == "chorsu_bozori"
    assert slugify("!!!") == "kamera"          # bo'sh qolmaydi
    assert slugify("A--B  C") == "a_b_c"


def test_unique_slug_takrorda_raqam_qoshadi():
    with get_db() as db:
        db.execute("INSERT INTO cameras (name, region, lat, lng, stream_url, "
                   "slug) VALUES ('S', 'T', 0, 0, '', 'sinov_slug')")
        try:
            assert unique_slug(db, "Sinov Slug") == "sinov_slug_2"
            # o'zining yozuvi hisobga olinmaydi (tahrirlash holati)
            row_id = db.execute("SELECT id FROM cameras WHERE slug = "
                                "'sinov_slug'").fetchone()[0]
            assert unique_slug(db, "Sinov Slug", exclude_id=row_id) == "sinov_slug"
        finally:
            db.execute("DELETE FROM cameras WHERE slug = 'sinov_slug'")

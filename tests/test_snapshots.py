"""Snapshot jadvali: faza tarqatish, oynalar, restart, klapan, semafor."""
import time

from core import health, snapshots


def _row(camera_id, slug="snap_test", ip="10.77.0.1", port=10554):
    return {"id": camera_id, "slug": slug, "ip": ip, "port": port,
            "username": "", "password_enc": "", "vendor": "",
            "rtsp_path": "", "external_id": ""}


def test_offset_barqaror_va_tekis():
    # bir xil id — har doim bir xil siljish (restartda jadval o'zgarmaydi)
    assert snapshots._offset(42, 300) == snapshots._offset(42, 300)
    # ketma-ket id'lar oraliqda tekis tarqaladi: 300 uyada 1000 kamera —
    # eng gavjum uyada bir nechtagina bo'lsin
    counts: dict[float, int] = {}
    for cam_id in range(1, 1001):
        off = snapshots._offset(cam_id, 300)
        counts[off] = counts.get(off, 0) + 1
    assert max(counts.values()) <= 12


def test_cold_interval_moslashadi():
    assert snapshots.cold_interval(200) == 300.0     # kichikda o'zgarmaydi
    assert snapshots.cold_interval(1000) == 300.0
    assert snapshots.cold_interval(5000) == 1000.0   # ~17 daqiqa
    assert snapshots.max_age() >= 3 * 300.0


def test_slot_oynada_bir_marta():
    now = 1_000_000.0
    slot = snapshots._slot(7, 300, now)
    assert slot <= now and now - slot < 300
    # oyna ichida slot o'zgarmaydi, keyingi oynada 300 ga siljiydi
    assert snapshots._slot(7, 300, slot + 299) == slot
    assert snapshots._slot(7, 300, slot + 300) == slot + 300


def test_xatoda_ham_oyna_belgilanadi(monkeypatch):
    snapshots._done.pop(9001, None)
    monkeypatch.setattr(snapshots, "capture", lambda row: False)
    assert snapshots._attempt(_row(9001)) is False
    assert 9001 in snapshots._done       # buzuq kamera tickda qayta urinilmaydi
    snapshots._done.pop(9001, None)


def test_restart_diskdagi_yangi_faylni_takror_olmaydi(tmp_path, monkeypatch):
    row = _row(9002, slug="restart_sinov")
    monkeypatch.setattr(snapshots, "SNAP_DIR", tmp_path)
    p = tmp_path / "restart_sinov.jpg"
    p.write_bytes(b"\xff\xd8jpeg")
    # xotira bo'sh (restart), fayl hozirgina yozilgan
    snapshots._done.pop(9002, None)
    snapshots.note_request(9002)                     # issiq pog'ona
    health._statuses[("10.77.0.1", 10554)] = True

    class _DB:
        def __enter__(self):
            return self
        def __exit__(self, *a):
            pass
        def execute(self, *a):
            class _C:
                def fetchall(self):
                    return [row]
            return _C()

    monkeypatch.setattr(snapshots, "get_db", lambda: _DB())
    due = snapshots._due_cameras()
    assert row not in due                            # mtime >= slot — bajarilgan
    assert 9002 in snapshots._done
    # fayl eskirsa — navbatga tushadi
    import os
    old = time.time() - 3600
    os.utime(p, (old, old))
    snapshots._done.pop(9002, None)
    assert any(r["id"] == 9002 for r in snapshots._due_cameras())
    snapshots._done.pop(9002, None)
    health._statuses.pop(("10.77.0.1", 10554), None)


def test_yetim_fayllar_tozalanadi(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "SNAP_DIR", tmp_path)
    old = time.time() - snapshots.ORPHAN_KEEP - 60
    # eski yetim — o'chadi; yangi yetim — bir hafta turadi
    eski = tmp_path / "yoq_kamera_eski.jpg"
    yangi = tmp_path / "yoq_kamera_yangi.jpg"
    eski.write_bytes(b"x")
    yangi.write_bytes(b"x")
    import os
    os.utime(eski, (old, old))
    # bazadagi kamera surati — yoshi qancha bo'lsa ham tegilmaydi
    from core.db import get_db
    with get_db() as db:
        slug = db.execute("SELECT slug FROM cameras LIMIT 1").fetchone()[0]
    bor = tmp_path / f"{slug}.jpg"
    bor.write_bytes(b"x")
    os.utime(bor, (old, old))

    snapshots._clean_orphans()
    assert not eski.exists()
    assert yangi.exists() and bor.exists()


def test_semafor_uchinchi_jonli_olishni_rad_etadi(monkeypatch):
    monkeypatch.setattr(snapshots, "capture", lambda row: False)
    with snapshots._live_sem:
        with snapshots._live_sem:
            # ikkala slot band — read() diskda fayl yo'q bo'lsa darhol bo'sh
            data, etag, at = snapshots.read(_row(9003, slug="yoq_slug"))
            assert data is None and etag == "" and at == 0.0
    # slot bo'shadi — endi jonli olishga uriniladi (capture=False -> baribir bo'sh)
    data, _, _ = snapshots.read(_row(9003, slug="yoq_slug"))
    assert data is None

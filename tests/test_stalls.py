"""Muzlash aniqlash: bayt hisobi qimirlamasa oqim o'lik.

TCP tekshiruvi (health) buni ko'rmaydi — registrator portga javob
beraveradi, kanal esa tasvir bermay qo'yishi mumkin.
"""
import pytest

from media import reconciler

NODE = {"id": 1, "name": "Asosiy", "api_base": "http://127.0.0.1:9997"}


@pytest.fixture()
def clean():
    reconciler._prev_bytes.clear()
    reconciler._stalled.clear()
    yield
    reconciler._prev_bytes.clear()
    reconciler._stalled.clear()


def _feed(monkeypatch, paths):
    monkeypatch.setattr(reconciler.sync, "list_active_paths", lambda api: paths)


def test_bayt_qimirlamasa_muzlagan(monkeypatch, clean):
    # 1-o'lchov: taqqoslash uchun avvalgi qiymat yo'q — hali muzlash emas.
    _feed(monkeypatch, {"kam_1": {"ready": True, "bytesReceived": 1000}})
    reconciler._check_stalls(NODE)
    assert reconciler.stalled_paths() == set()

    # 2-o'lchov: bayt o'zgarmadi — oqim qotgan.
    reconciler._check_stalls(NODE)
    assert reconciler.stalled_paths() == {"kam_1"}
    assert reconciler.stalled_count(1) == 1

    # Bayt yana oqdi — tiklandi.
    _feed(monkeypatch, {"kam_1": {"ready": True, "bytesReceived": 5000}})
    reconciler._check_stalls(NODE)
    assert reconciler.stalled_paths() == set()


def test_hali_ulanmagan_yol_muzlagan_hisoblanmaydi(monkeypatch, clean):
    """ready=False — kamera hali ulanmoqda, bu muzlash emas."""
    _feed(monkeypatch, {"kam_1": {"ready": False, "bytesReceived": 0}})
    reconciler._check_stalls(NODE)
    reconciler._check_stalls(NODE)
    assert reconciler.stalled_paths() == set()


def test_yopilgan_oqim_royxatdan_chiqadi(monkeypatch, clean):
    _feed(monkeypatch, {"kam_1": {"ready": True, "bytesReceived": 10}})
    reconciler._check_stalls(NODE)
    reconciler._check_stalls(NODE)
    assert reconciler.stalled_paths() == {"kam_1"}

    _feed(monkeypatch, {})              # tomoshabin ketdi, yo'l yopildi
    reconciler._check_stalls(NODE)
    assert reconciler.stalled_paths() == set()


def test_api_javob_bermasa_holat_ozgarmaydi(monkeypatch, clean):
    _feed(monkeypatch, {"kam_1": {"ready": True, "bytesReceived": 10}})
    reconciler._check_stalls(NODE)
    reconciler._check_stalls(NODE)
    assert reconciler.stalled_paths() == {"kam_1"}

    _feed(monkeypatch, None)            # MediaMTX javob bermadi
    reconciler._check_stalls(NODE)
    assert reconciler.stalled_paths() == {"kam_1"}   # eski holat saqlanadi


def test_olik_tugun_tez_tsiklda_otkazib_yuboriladi(monkeypatch, clean):
    """Javob bermayotgan tugunning 4 soniyalik timeout'i har 5 soniyalik
    tsiklni cho'zib yuborishi mumkin edi."""
    monkeypatch.setattr(reconciler, "_nodes", lambda: [NODE, {**NODE, "id": 2}])
    called = []
    monkeypatch.setattr(reconciler, "_check_stalls",
                        lambda node: called.append(node["id"]))
    reconciler._reachable.clear()
    reconciler._reachable.add(2)                 # faqat 2-tugun tirik
    try:
        reconciler._watch_active()
    finally:
        reconciler._reachable.clear()
    assert called == [2]

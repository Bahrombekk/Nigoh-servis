"""Muzlash aniqlash: bayt hisobi STALL_AFTER davomida qimirlamasa oqim o'lik.

TCP tekshiruvi (health) buni ko'rmaydi — registrator portga javob
beraveradi, kanal esa tasvir bermay qo'yishi mumkin.

O'lchov VAQT bo'yicha, tsikl bo'yicha emas: kamera ma'lumotni portlash
bilan yuborishi (uzun GOP) normal holat va ikki portlash orasidagi
jimlik muzlash emas.
"""
import pytest

from media import reconciler

NODE = {"id": 1, "name": "Asosiy", "api_base": "http://127.0.0.1:9997"}


class Soat:
    """Sinov soati — `time.monotonic` o'rniga qo'yiladi."""

    def __init__(self):
        self.hozir = 1000.0

    def monotonic(self):
        return self.hozir

    def surish(self, sekund):
        self.hozir += sekund


@pytest.fixture()
def soat(monkeypatch):
    s = Soat()
    monkeypatch.setattr(reconciler.time, "monotonic", s.monotonic)
    return s


@pytest.fixture()
def clean():
    reconciler._prev_bytes.clear()
    reconciler._stalled.clear()
    yield
    reconciler._prev_bytes.clear()
    reconciler._stalled.clear()


def _feed(monkeypatch, paths):
    monkeypatch.setattr(reconciler.sync, "list_active_paths", lambda api: paths)


def test_bayt_qimirlamasa_muzlagan(monkeypatch, clean, soat):
    # 1-o'lchov: taqqoslash uchun avvalgi qiymat yo'q — hali muzlash emas.
    _feed(monkeypatch, {"kam_1": {"ready": True, "bytesReceived": 1000}})
    reconciler._check_stalls(NODE)
    assert reconciler.stalled_paths() == set()

    # Navbatdagi tsikl (5 s): bayt o'zgarmadi, lekin muddat hali to'lmagan.
    soat.surish(reconciler.STALL_INTERVAL)
    reconciler._check_stalls(NODE)
    assert reconciler.stalled_paths() == set()

    # STALL_AFTER to'ldi — endi oqim aniq qotgan.
    soat.surish(reconciler.STALL_AFTER)
    reconciler._check_stalls(NODE)
    assert reconciler.stalled_paths() == {"kam_1"}
    assert reconciler.stalled_count(1) == 1

    # Bayt yana oqdi — tiklandi.
    _feed(monkeypatch, {"kam_1": {"ready": True, "bytesReceived": 5000}})
    reconciler._check_stalls(NODE)
    assert reconciler.stalled_paths() == set()


def test_portlash_bilan_keladigan_kamera_muzlagan_hisoblanmaydi(
        monkeypatch, clean, soat):
    """Regressiya: uzun GOP'li kamera har 6-8 soniyada bir portlab beradi.

    Ilgari ketma-ket ikki tsikl taqqoslanardi va shunday kamera tinmay
    "muzladi -> tiklandi" bo'lib turardi (o'lchov: bitta Dahua kanalida
    12 marta ketma-ket, har biri 5 soniyalik). Tomoshabinga SSE orqali
    `stalled` yuborilardi, ya'ni sog'lom kamera muammoli bo'lib ko'rinardi.
    """
    bayt = 1000
    for _ in range(20):                      # ~100 soniya, har 5 soniyada tsikl
        _feed(monkeypatch, {"kam_1": {"ready": True, "bytesReceived": bayt}})
        reconciler._check_stalls(NODE)
        assert reconciler.stalled_paths() == set()
        soat.surish(reconciler.STALL_INTERVAL)
        # Har ikkinchi tsiklda (10 s) yangi portlash keladi.
        bayt += 675_000 if _ % 2 else 0


def test_hali_ulanmagan_yol_muzlagan_hisoblanmaydi(monkeypatch, clean, soat):
    """ready=False — kamera hali ulanmoqda, bu muzlash emas."""
    _feed(monkeypatch, {"kam_1": {"ready": False, "bytesReceived": 0}})
    reconciler._check_stalls(NODE)
    soat.surish(2 * reconciler.STALL_AFTER)
    reconciler._check_stalls(NODE)
    assert reconciler.stalled_paths() == set()


def test_sekin_ochilgan_yol_darhol_muzlagan_bolmaydi(monkeypatch, clean, soat):
    """Ulanish uzoq davom etsa, tayyor bo'lgan zahoti muzlagan deyilmasin.

    Muzlash soati faqat yo'l TAYYOR bo'lganda yurishi kerak. Aks holda
    sekin kamera (o'lchov: relay 15 soniyada ulangan) `ready` bo'lgan
    birinchi tsiklda "muzlagan" bo'lib chiqardi — chunki soat u
    ulanayotgan paytda ishlab bo'lgan bo'lardi.
    """
    _feed(monkeypatch, {"kam_1": {"ready": False, "bytesReceived": 0}})
    for _ in range(8):                       # 40 soniya "ulanmoqda"
        reconciler._check_stalls(NODE)
        soat.surish(reconciler.STALL_INTERVAL)

    _feed(monkeypatch, {"kam_1": {"ready": True, "bytesReceived": 0}})
    reconciler._check_stalls(NODE)
    assert reconciler.stalled_paths() == set()

    # Tayyor bo'lgandan keyin ham bayt kelmasa — endi haqiqiy muzlash.
    soat.surish(reconciler.STALL_AFTER)
    reconciler._check_stalls(NODE)
    assert reconciler.stalled_paths() == {"kam_1"}


def _muzlat(monkeypatch, soat, bayt=10):
    """Yo'lni muzlagan holatga keltiradi."""
    _feed(monkeypatch, {"kam_1": {"ready": True, "bytesReceived": bayt}})
    reconciler._check_stalls(NODE)
    soat.surish(reconciler.STALL_AFTER)
    reconciler._check_stalls(NODE)


def test_yopilgan_oqim_royxatdan_chiqadi(monkeypatch, clean, soat):
    _muzlat(monkeypatch, soat)
    assert reconciler.stalled_paths() == {"kam_1"}

    _feed(monkeypatch, {})              # tomoshabin ketdi, yo'l yopildi
    reconciler._check_stalls(NODE)
    assert reconciler.stalled_paths() == set()


def test_api_javob_bermasa_holat_ozgarmaydi(monkeypatch, clean, soat):
    _muzlat(monkeypatch, soat)
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

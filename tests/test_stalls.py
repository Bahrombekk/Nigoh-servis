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


# ---------- ochilmayotgan yo'l: transport sinoviga buyurtma ----------
#
# Bu muzlashdan BOSHQA nosozlik: yo'l hech qachon "tayyor" bo'lmaydi,
# bayt hisobi esa har urinishda noldan boshlanadi — yuqoridagi mantiq
# buni umuman ko'rmaydi. Eng ko'p uchragan sababi: kamera RTSP'ni TCP
# orqali bermaydi (media/transport.py).

@pytest.fixture()
def sinovlar(monkeypatch):
    """`transport.request` o'rniga — chaqirilganlar ro'yxati."""
    buyurtma = []
    monkeypatch.setattr(reconciler.transport, "request",
                        lambda slug: buyurtma.append(slug) or True)
    reconciler._not_ready.clear()
    yield buyurtma
    reconciler._not_ready.clear()


def test_tayyor_bolmagan_yol_sinovga_buyuriladi(monkeypatch, clean, soat, sinovlar):
    yol = {"kam_1": {"ready": False, "bytesReceived": 0}}
    _feed(monkeypatch, yol)
    reconciler._check_stalls(NODE)
    assert sinovlar == []                       # hali erta — ulanayotgan bo'lishi mumkin

    for _ in range(int(reconciler.NOT_READY_AFTER / reconciler.STALL_INTERVAL) + 1):
        soat.surish(reconciler.STALL_INTERVAL)
        reconciler._check_stalls(NODE)
    # Takror buyurtma zarar qilmaydi (transport.request o'zi tormozlaydi),
    # muhimi — aynan shu kamera va faqat muddat to'lgandan keyin.
    assert set(sinovlar) == {"kam_1"}


def test_yol_yoqolib_tursa_ham_hisob_saqlanadi(monkeypatch, clean, soat, sinovlar):
    """Ochilmayotgan yo'l MediaMTX ro'yxatidan vaqti-vaqti bilan
    butunlay yo'qoladi (manba o'ldi -> talab bo'yicha yo'l o'chdi).
    Hisoblagich o'shanda nolga qaytsa, muddat hech qachon to'lmaydi —
    ishlab chiqarishda aynan shu bo'lgan: 7 daqiqada bitta ham sinov
    ishga tushmagan."""
    bor = {"kam_1": {"ready": False, "bytesReceived": 0}}
    for _ in range(int(reconciler.NOT_READY_AFTER / reconciler.STALL_INTERVAL) + 2):
        _feed(monkeypatch, bor)
        reconciler._check_stalls(NODE)
        soat.surish(reconciler.STALL_INTERVAL)
        _feed(monkeypatch, {})                  # yo'l yo'qoldi
        reconciler._check_stalls(NODE)
        soat.surish(reconciler.STALL_INTERVAL)
    assert set(sinovlar) == {"kam_1"}


def test_tayyor_bolgan_yol_sinovga_tushmaydi(monkeypatch, clean, soat, sinovlar):
    _feed(monkeypatch, {"kam_1": {"ready": False, "bytesReceived": 0}})
    reconciler._check_stalls(NODE)
    for _ in range(20):
        soat.surish(reconciler.STALL_INTERVAL)
        _feed(monkeypatch, {"kam_1": {"ready": True, "bytesReceived": 5000}})
        reconciler._check_stalls(NODE)
    assert sinovlar == []


def test_devor_yoli_sinovga_tushmaydi(monkeypatch, clean, soat, sinovlar):
    """`wall_...` — mozaika, kamera emas: unda transport degan tushuncha yo'q."""
    _feed(monkeypatch, {"wall_abc": {"ready": False, "bytesReceived": 0}})
    for _ in range(int(reconciler.NOT_READY_AFTER / reconciler.STALL_INTERVAL) + 2):
        soat.surish(reconciler.STALL_INTERVAL)
        reconciler._check_stalls(NODE)
    assert sinovlar == []


def test_ogirilgan_yol_kamera_slugi_bilan_sinaladi(monkeypatch, clean, soat, sinovlar):
    """`<slug>_sub_h264` — o'sha kameraning ko'rinishi; transport butun
    qurilmaga tegishli, shuning uchun asosiy slug sinaladi."""
    _feed(monkeypatch, {"kam_1_sub_h264": {"ready": False, "bytesReceived": 0}})
    for _ in range(int(reconciler.NOT_READY_AFTER / reconciler.STALL_INTERVAL) + 2):
        soat.surish(reconciler.STALL_INTERVAL)
        reconciler._check_stalls(NODE)
    assert set(sinovlar) == {"kam_1"}


def test_buzuq_kadrlar_ham_sinovga_olib_boradi(monkeypatch, clean, soat, sinovlar):
    """Yo'l "tayyor", bayt ham kelyapti — lekin kadrlar buzuq.

    Ishlab chiqarishda o'lchangan holat: kamera TCP'da sekundiga
    1200-1700 RTP paket yo'qotgan, MediaMTX "invalid FU-A packet" deb
    kadrni yig'olmagan va HLS segmenti chiqmagan — tomoshabin faqat 500
    ko'rgan. Na muzlash (bayt kelyapti), na "tayyor emas" tekshiruvi
    buni ko'rmaydi.
    """
    xato = {"v": 100}
    bayt = {"v": 1000}

    def yol():
        return {"kam_1": {"ready": True, "bytesReceived": bayt["v"],
                          "inboundFramesInError": xato["v"]}}

    _feed(monkeypatch, yol())
    reconciler._check_stalls(NODE)
    for _ in range(int(reconciler.NOT_READY_AFTER / reconciler.STALL_INTERVAL) + 2):
        soat.surish(reconciler.STALL_INTERVAL)
        xato["v"] += 50          # buzuq kadrlar o'sib boryapti
        bayt["v"] += 100000      # oqim esa "kelyapti"
        _feed(monkeypatch, yol())
        reconciler._check_stalls(NODE)
    assert set(sinovlar) == {"kam_1"}
    assert reconciler.stalled_paths() == set()   # bu muzlash EMAS


def test_sogom_oqim_sinovga_tushmaydi(monkeypatch, clean, soat, sinovlar):
    """Buzuq kadr o'smasa — hammasi joyida, hech narsa qilinmaydi."""
    bayt = {"v": 1000}

    for _ in range(int(reconciler.NOT_READY_AFTER / reconciler.STALL_INTERVAL) + 4):
        _feed(monkeypatch, {"kam_1": {"ready": True, "bytesReceived": bayt["v"],
                                      "inboundFramesInError": 7}})
        reconciler._check_stalls(NODE)
        soat.surish(reconciler.STALL_INTERVAL)
        bayt["v"] += 100000
    assert sinovlar == []

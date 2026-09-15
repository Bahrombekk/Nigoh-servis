"""Sub oqim yaroqsizligini tizim O'ZI aniqlaydi va o'zi bekor qiladi.

Amalda uchragan holat: registratorning 8 kanalidan ikkitasida ikkinchi
oqim yoqilmagan edi — devorda o'sha kataklar bo'sh qolardi va buni
qo'lda topib, qo'lda belgilash kerak bo'lardi.

Shartlar ataylab qattiq: sog'lom kamerani noto'g'ri belgilash tomoshani
og'irlashtiradi (o'lchovda asosiy oqim 7,88 Mbit/s, sub 1,20 Mbit/s).
"""
import pytest

from media import reconciler

NODE = {"id": 1, "name": "Asosiy", "api_base": "http://127.0.0.1:9997"}


class Soat:
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
def toza():
    for t in (reconciler._sub_ok, reconciler._sub_zero,
              reconciler._sub_tekshiruvda):
        t.clear()
    yield
    for t in (reconciler._sub_ok, reconciler._sub_zero,
              reconciler._sub_tekshiruvda):
        t.clear()


@pytest.fixture()
def shubha(monkeypatch):
    """Tekshiruvga yuborilgan yo'llar — hukm emas, shubha."""
    olingan: list[str] = []

    class SoxtaThread:
        def __init__(self, target=None, args=(), daemon=None):
            self._args = args

        def start(self):
            olingan.extend(self._args[0])

    monkeypatch.setattr(reconciler.threading, "Thread", SoxtaThread)
    return olingan


@pytest.fixture()
def yozuvlar(monkeypatch):
    """`set_sub_bad` chaqiruvlarini ushlaymiz — bazaga tegmaymiz."""
    olingan: list[tuple[list[str], bool]] = []

    def soxta(slugs, bad):
        olingan.append((list(slugs), bad))
        return list(slugs)

    monkeypatch.setattr(reconciler, "set_sub_bad", soxta)
    return olingan


def _issiq(monkeypatch, nomlar):
    monkeypatch.setattr(reconciler.sync, "is_warm", lambda n: n in nomlar)


def test_soralgan_sub_kelmasa_tekshiruvga_yuboriladi(
        monkeypatch, toza, soat, yozuvlar, shubha):
    """Kuzatuv hukm chiqarmaydi — tekshiruvga yuboradi."""
    _issiq(monkeypatch, {"kam_sub"})
    yollar = {"kam_sub": {"ready": False, "bytesReceived": 0}}

    # Birinchi ko'rish — soat endi boshlandi.
    reconciler._check_sub_health(NODE, yollar)
    assert shubha == []

    # Muddat to'lmagan.
    soat.surish(reconciler.SUB_DEAD_AFTER - 1)
    reconciler._check_sub_health(NODE, yollar)
    assert shubha == []

    # SUB_DEAD_AFTER to'ldi — shubha bor, lekin bayroq HALI qo'yilmaydi.
    soat.surish(2)
    reconciler._check_sub_health(NODE, yollar)
    assert shubha == ["kam_sub"]
    assert yozuvlar == []

    # Takror yuborilmaydi (tekshiruv tugamaguncha).
    soat.surish(reconciler.SUB_DEAD_AFTER * 2)
    reconciler._check_sub_health(NODE, yollar)
    assert shubha == ["kam_sub"]


def test_soralmagan_yol_belgilanmaydi(monkeypatch, toza, soat, yozuvlar, shubha):
    """Hech kim so'ramagan yo'l tayyor emasligi NORMAL holat.

    sourceOnDemand yo'lni tomoshabin yo'qligida ochmaydi — buni
    nosozlik deb hisoblash hamma kamerani yaroqsiz qilib qo'yardi.
    """
    _issiq(monkeypatch, set())
    yollar = {"kam_sub": {"ready": False, "bytesReceived": 0}}
    for _ in range(5):
        soat.surish(reconciler.SUB_DEAD_AFTER)
        reconciler._check_sub_health(NODE, yollar)
    assert yozuvlar == []
    assert shubha == []


def test_bir_marta_ishlagan_sub_keyin_yopilsa_belgilanmaydi(
        monkeypatch, toza, soat, yozuvlar):
    """Regressiya: tomoshabin ketgach sourceOnDemand yo'lni yopadi.

    Shunda `ready` False bo'ladi va bayt hisobi nolga tushadi — bu
    nosozlikka o'xshab ko'rinadi, lekin sub aslida sog'lom.
    """
    _issiq(monkeypatch, {"kam_sub"})
    reconciler._check_sub_health(
        NODE, {"kam_sub": {"ready": True, "bytesReceived": 9000}})
    assert yozuvlar == [(["kam"], False)]      # ishlayapti -> bayroq olinadi
    yozuvlar.clear()

    # Endi yopildi: tayyor emas, hisob nolda — lekin biz uni ko'rgan edik.
    yopiq = {"kam_sub": {"ready": False, "bytesReceived": 0}}
    for _ in range(5):
        soat.surish(reconciler.SUB_DEAD_AFTER)
        reconciler._check_sub_health(NODE, yopiq)
    assert yozuvlar == []


def test_ishlayotgan_sub_bayrogi_olinadi(monkeypatch, toza, soat, yozuvlar):
    """Operator registratorda sub'ni yoqsa — tizim o'zi bekor qiladi."""
    _issiq(monkeypatch, {"kam_sub"})
    reconciler._check_sub_health(
        NODE, {"kam_sub": {"ready": True, "bytesReceived": 1}})
    assert yozuvlar == [(["kam"], False)]


def test_ogirish_chiqishi_hisobga_olinmaydi(monkeypatch, toza, soat, yozuvlar, shubha):
    """`_sub_h264` — bizning o'girish CHIQISHIMIZ, kameraning oqimi emas."""
    _issiq(monkeypatch, {"kam_sub_h264"})
    yollar = {"kam_sub_h264": {"ready": False, "bytesReceived": 0}}
    for _ in range(5):
        soat.surish(reconciler.SUB_DEAD_AFTER)
        reconciler._check_sub_health(NODE, yollar)
    assert yozuvlar == []


def test_asosiy_yol_hisobga_olinmaydi(monkeypatch, toza, soat, yozuvlar, shubha):
    _issiq(monkeypatch, {"kam"})
    yollar = {"kam": {"ready": False, "bytesReceived": 0}}
    for _ in range(5):
        soat.surish(reconciler.SUB_DEAD_AFTER)
        reconciler._check_sub_health(NODE, yollar)
    assert yozuvlar == []


def test_kadr_beradigan_kamera_belgilanmaydi(monkeypatch, toza, yozuvlar):
    """Regressiya: kuzatuvning o'zi yetarli emas.

    Jonli tizimda sinaganda aynan shu yuz berdi — sog'lom kanalning sub
    manzili so'ralgan edi, lekin tomoshabin ulanmagani uchun yo'l bo'sh
    turardi va eski mantiq uni yaroqsiz deb belgilagan edi.
    """
    monkeypatch.setattr(reconciler, "cameras_by_slug", lambda slugs: [
        {"id": 1, "slug": "kam", "ip": "10.0.0.1", "port": 554,
         "username": "a", "password_enc": b"", "sub_path": "/sub"}])
    monkeypatch.setattr(reconciler, "_sub_kadr_beradimi", lambda cam: True)

    reconciler._sub_tasdiqla(["kam_sub"])
    assert yozuvlar == []
    assert reconciler._sub_tekshiruvda == set()


def test_kadr_bermasa_belgilanadi(monkeypatch, toza, yozuvlar):
    monkeypatch.setattr(reconciler, "cameras_by_slug", lambda slugs: [
        {"id": 1, "slug": "kam", "ip": "10.0.0.1", "port": 554,
         "username": "a", "password_enc": b"", "sub_path": "/sub"}])
    monkeypatch.setattr(reconciler, "_sub_kadr_beradimi", lambda cam: False)

    reconciler._sub_tasdiqla(["kam_sub"])
    assert yozuvlar == [(["kam"], True)]
    assert reconciler._sub_tekshiruvda == set()


def test_describe_javob_bersa_ham_kadr_shart(monkeypatch, toza):
    """Regressiya: DESCRIBE yolg'on gapiradi.

    Registratorning ikki kanali DESCRIBE'ga javob berib, SDP'da sub
    oqimni (hevc 704x576) e'lon qilardi, lekin bitta ham paket bermasdi.
    Shuning uchun hukm `sync.kadr_keladimi` ga tayanadi — paket
    darajasida, DESCRIBE'ga emas.
    """
    chaqirildi = []
    monkeypatch.setattr(reconciler.sync, "kadr_keladimi",
                        lambda url, *a, **k: chaqirildi.append(url) or False)
    monkeypatch.setattr(reconciler.security, "decrypt", lambda v: "p")
    cam = {"ip": "10.0.0.1", "port": 554, "username": "a",
           "password_enc": b"", "sub_path": "/sub"}
    assert reconciler._sub_kadr_beradimi(cam) is False
    assert chaqirildi and "/sub" in chaqirildi[0]


def test_udp_kamera_tcp_bilan_ayblanmaydi(monkeypatch, toza):
    """Regressiya: UDP kerak bo'lgan kamera "sub yo'q" deb belgilanmasin.

    Kameralarning bir qismi RTSP'ni TCP'da umuman bermaydi va tizim
    ularni UDP'ga o'tkazadi (`cameras.rtsp_udp`, media/transport.py).
    Tekshiruv qat'iy TCP bilan ketsa, o'sha kameraning sub oqimi
    "kadr bermayapti" bo'lib chiqardi — aslida transport noto'g'ri
    tanlangan bo'lardi, va kamera abadiy og'ir asosiy oqimga o'tib
    ketardi.
    """
    chaqiruv = {}
    monkeypatch.setattr(reconciler.sync, "kadr_keladimi",
                        lambda url, *a, **k: chaqiruv.update(k) or True)
    monkeypatch.setattr(reconciler.security, "decrypt", lambda v: "p")
    cam = {"ip": "10.0.0.1", "port": 554, "username": "a", "password_enc": b"",
           "sub_path": "/sub", "rtsp_udp": 1}
    reconciler._sub_kadr_beradimi(cam)
    assert chaqiruv.get("udp") is True

    chaqiruv.clear()
    cam["rtsp_udp"] = 0
    reconciler._sub_kadr_beradimi(cam)
    assert chaqiruv.get("udp") is False


def test_transport_sinovi_ketayotganda_hukm_kutadi(
        monkeypatch, toza, soat, yozuvlar, shubha):
    """Sinov davomida kadr kelmasligi NORMAL — u aynan shuni o'lchayapti.

    Sinov kamerani UDP'ga o'tkazsa sub o'z-o'zidan ishlab ketishi
    mumkin, shuning uchun hukm shoshilmaydi.
    """
    _issiq(monkeypatch, {"kam_sub"})
    monkeypatch.setattr(reconciler.transport, "busy", lambda slug: slug == "kam")
    yollar = {"kam_sub": {"ready": False, "bytesReceived": 0}}
    for _ in range(5):
        soat.surish(reconciler.SUB_DEAD_AFTER)
        reconciler._check_sub_health(NODE, yollar)
    assert shubha == []

    # Sinov tugadi — endi odatdagi tartibda shubhaga tushadi.
    monkeypatch.setattr(reconciler.transport, "busy", lambda slug: False)
    reconciler._check_sub_health(NODE, yollar)
    soat.surish(reconciler.SUB_DEAD_AFTER + 1)
    reconciler._check_sub_health(NODE, yollar)
    assert shubha == ["kam_sub"]

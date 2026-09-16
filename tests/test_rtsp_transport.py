"""RTSP transporti: TCP standart, TCP bermaydigan kamera uchun UDP.

Nima uchun bu testlar bor. Kameralarning bir qismi RTSP'ni TCP
(interleaved) orqali bermaydi — PLAY'ga 200 OK qaytarib, ulanishni 1-2
soniyada o'zi yopadi. O'lchov: shu o'rnatmadagi 155 ta kameradan 42 tasi
shunday, ularning aksariyati UDP'da bemalol ishlaydi. Tashqaridan bu
"kamera ochilmayapti" bo'lib ko'rinardi (HLS muxeri segment bermay
o'ladi -> brauzerga bo'sh tanali 500).

Tanlov ikki tomonga ham xato bo'lmasligi kerak:

  * hamma kamera UDP'ga o'tib ketsa — paket yo'qoladi, tasvir sinadi;
  * UDP kerak bo'lgani TCP'da qolsa — tasvir umuman yo'q.

Shuning uchun qaror kamera bo'yicha bazada saqlanadi va shu testlar uni
qo'riqlaydi.
"""
import pytest
import yaml

from media import sync, transport

KAM = {"slug": "kam_1", "ip": "10.0.0.1", "port": 554, "rtsp_path": "/s1",
       "username": "u", "password": "p", "always_on": False}


def test_standart_tcp():
    assert sync.source_path(KAM)["rtspTransport"] == "tcp"


def test_udp_kamera_relay_orqali_tortiladi():
    """UDP'ni MediaMTX yomon o'qiydi (paketlar tartibini tiklamaydi) —
    shuning uchun bunday kamera FFmpeg relay orqali ketadi."""
    conf = sync.source_path({**KAM, "rtsp_udp": True})
    assert "runOnDemand" in conf
    # Parol konfiguratsiyaga tushmasin — launcher uni bazadan oladi.
    assert "source" not in conf


def test_sub_oqim_ham_udp():
    """Transport QURILMAGA tegishli — sub oqim ham xuddi shunday tortiladi."""
    sub = sync.sub_variant({**KAM, "rtsp_udp": True, "sub_path": "/s2"})
    assert sub["rtsp_udp"] is True
    assert "runOnDemand" in sync.source_path(sub)


def test_yol_turi_ozgarganda_toliq_almashtiriladi():
    """`source` -> `runOnDemand` PATCH bilan qilinsa eski `source` qolib
    ketardi: MediaMTX ham, launcher ham kamerani tortib, oqim ikkalasi
    orasida sakrardi."""
    tog = sync.source_path(KAM)                       # MediaMTX o'zi tortadi
    relay = sync.source_path({**KAM, "rtsp_udp": True})   # FFmpeg tortadi
    assert sync.needs_replace(tog, relay) is True
    assert sync.needs_replace(relay, tog) is True
    assert sync.needs_replace(tog, tog) is False
    assert sync.needs_replace(relay, relay) is False


def test_ffmpeg_kirishi_transportga_qarab():
    tcp = sync.relay_args("rtsp://a/1", "rtsp://b/1")
    udp = sync.relay_args("rtsp://a/1", "rtsp://b/1", udp=True)
    assert "-rtsp_transport" in tcp and tcp[tcp.index("-rtsp_transport") + 1] == "tcp"
    assert udp[udp.index("-rtsp_transport") + 1] == "udp"
    # UDP'da katta qabul buferi bo'lishi SHART — usiz keyframe portlashida
    # paket tushadi va tasvir sinadi.
    assert "-buffer_size" in udp
    assert "-buffer_size" not in tcp


def test_ogirish_ham_transportni_oladi():
    args = sync.transcode_args("rtsp://a/1", "rtsp://b/1", gpu=False, udp=True)
    assert args[args.index("-rtsp_transport") + 1] == "udp"


def test_udp_buferi_konfiguratsiyada():
    conf = yaml.safe_load(sync.build_config([]))
    assert conf["udpReadBufferSize"] == sync.UDP_READ_BUFFER
    # MediaMTX RTSP SERVERI (ichki publish) baribir TCP — bu boshqa sozlama.
    assert conf["rtspTransports"] == ["tcp"]


# ---------- avtomatik aniqlash ----------

class _Baza:
    """`transport.check` uchun eng kichik baza qatori."""

    def __init__(self, rtsp_udp=0):
        self.row = {"id": 1, "slug": "kam_1", "external_id": "", "ip": "10.0.0.1",
                    "port": 554, "username": "u", "password_enc": "x",
                    "rtsp_path": "/s1", "rtsp_udp": rtsp_udp}


@pytest.fixture
def qolda(monkeypatch):
    """Kadr o'lchovini va bazaga yozishni qo'lda boshqaramiz."""
    yozilgan = {}

    def remember(row, udp):
        yozilgan["udp"] = udp

    monkeypatch.setattr(transport, "_remember", remember)
    monkeypatch.setattr(transport.security, "decrypt", lambda _: "p")
    return yozilgan


def _kadrlar(monkeypatch, tcp: int, udp: int,
             tcp_buzuq: int = 0, udp_buzuq: int = 0):
    """`_frames` endi (kadr, buzuq kadr) juftligini qaytaradi."""
    monkeypatch.setattr(
        transport, "_frames",
        lambda url, tr: (tcp, tcp_buzuq) if tr == "tcp" else (udp, udp_buzuq))


def test_tcp_yiqilsa_udp_ga_otadi(monkeypatch, qolda):
    monkeypatch.setattr(transport, "_camera", lambda slug: _Baza().row)
    _kadrlar(monkeypatch, tcp=1, udp=190)
    assert transport.check("kam_1") == "udp"
    assert qolda["udp"] is True


def test_tcp_ishlasa_tegilmaydi(monkeypatch, qolda):
    monkeypatch.setattr(transport, "_camera", lambda slug: _Baza().row)
    _kadrlar(monkeypatch, tcp=200, udp=0)
    assert transport.check("kam_1") is None
    assert qolda == {}


def test_ikkalasi_yiqilsa_tegilmaydi(monkeypatch, qolda):
    """Kamera o'chiq — bu transport muammosi emas, holat o'zgarmasin."""
    monkeypatch.setattr(transport, "_camera", lambda slug: _Baza().row)
    _kadrlar(monkeypatch, tcp=0, udp=0)
    assert transport.check("kam_1") is None
    assert qolda == {}


def test_udp_dan_tcp_ga_qaytadi(monkeypatch, qolda):
    """Kamera tuzalsa sifatliroq transportga qaytamiz."""
    monkeypatch.setattr(transport, "_camera", lambda slug: _Baza(rtsp_udp=1).row)
    _kadrlar(monkeypatch, tcp=200, udp=0)
    assert transport.check("kam_1") == "tcp"
    assert qolda["udp"] is False


def test_takror_sinov_tormozlanadi(monkeypatch):
    """Ochilmayotgan kamera har 5 soniyada ikkita FFmpeg ko'tarmasin."""
    chaqirildi = []
    monkeypatch.setattr(transport, "check", lambda slug: chaqirildi.append(slug))
    transport._last_try.clear()
    transport._busy.clear()
    assert transport.request("kam_1") is True
    assert transport.request("kam_1") is False


def test_goh_ishlaydigan_tcp_ham_yetarli_emas(monkeypatch, qolda):
    """TCP "goh ishlaydi" bo'lsa — tomoshabin uchun bu ishlamaydi degani.

    O'lchov: bir martalik sinovda TCP "ishlaydi" chiqqan kamerada
    tomoshabin darajasida 30 ta so'rovdan atigi 6 tasi ochildi. Shuning
    uchun bo'sag'a — ketma-ket urinishlarning HAMMASI.
    """
    monkeypatch.setattr(transport, "_camera", lambda slug: _Baza().row)
    javoblar = {"tcp": iter([(200, 0), (0, 0)]),
                "udp": iter([(190, 0), (195, 0)])}
    monkeypatch.setattr(transport, "_frames",
                        lambda url, tr: next(javoblar[tr]))
    assert transport.check("kam_1") == "udp"
    assert qolda["udp"] is True


def test_goh_ishlaydigan_udp_ga_otilmaydi(monkeypatch, qolda):
    """Ikkinchi transport ham beqaror bo'lsa — o'tishning ma'nosi yo'q."""
    monkeypatch.setattr(transport, "_camera", lambda slug: _Baza().row)
    javoblar = {"tcp": iter([(0, 0)]), "udp": iter([(190, 0), (0, 0)])}
    monkeypatch.setattr(transport, "_frames",
                        lambda url, tr: next(javoblar[tr]))
    assert transport.check("kam_1") is None
    assert qolda == {}


def test_sinov_kameraga_ortiqcha_ulanish_ochmaydi(monkeypatch, qolda):
    """Sinov qimmat: har ulanish kameradan oqim tortadi.

    Ikkala transport ham o'lchanadi (sekinlashganini shusiz bilib
    bo'lmaydi), lekin har biriga STABLE_TRIES tadan ortiq emas. Sinovning
    o'zi esa faqat yo'l allaqachon ochilmayotganda chaqiriladi."""
    monkeypatch.setattr(transport, "_camera", lambda slug: _Baza().row)
    urilgan = []

    def frames(url, tr):
        urilgan.append(tr)
        return 200, 0

    monkeypatch.setattr(transport, "_frames", frames)
    assert transport.check("kam_1") is None
    assert urilgan.count("tcp") <= transport.STABLE_TRIES
    assert urilgan.count("udp") <= transport.STABLE_TRIES


def test_olik_kamera_holatni_ozgartirmaydi_va_tez_toxtaydi(monkeypatch, qolda):
    """Ikkala transport ham bermasa — bu kamera muammosi, transport emas.
    Bunda urinishlar birinchi xatodayoq to'xtashi kerak."""
    monkeypatch.setattr(transport, "_camera", lambda slug: _Baza().row)
    urilgan = []

    def frames(url, tr):
        urilgan.append(tr)
        return 0, 0

    monkeypatch.setattr(transport, "_frames", frames)
    assert transport.check("kam_1") is None
    assert qolda == {}
    assert len(urilgan) == 2          # har transportga bittadan, ko'p emas


def test_bir_vaqtda_kop_sinov_ketmaydi(monkeypatch):
    """Nosozlik odatda yakka emas. Hammasi birdan sinovga tushsa o'nlab
    FFmpeg tarmoqni bosadi va sinov o'z natijasini o'zi buzadi."""
    import threading

    boshlandi = threading.Event()
    monkeypatch.setattr(transport, "MAX_PARALLEL", 1)
    monkeypatch.setattr(transport, "check", lambda slug: boshlandi.wait(2))
    transport._last_try.clear()
    transport._busy.clear()
    try:
        assert transport.request("kam_1") is True
        assert transport.request("kam_2") is False      # navbat to'la
        boshlandi.set()
    finally:
        boshlandi.set()
    # Navbat to'lgani "sinaldi" emas — kam_2 keyinroq qayta urinishi kerak.
    assert "kam_2" not in transport._last_try


def test_sekinlashgan_transport_ham_almashtiriladi(monkeypatch, qolda):
    """TCP "ishlayapti", lekin bir necha baravar sekin — bu ham nosozlik.

    O'lchov: bitta kamera TCP'da 8 soniyada 28 kadr, UDP'da 188 kadr
    bergan. TCP rasman ishlagan, lekin MediaMTX HLS segmentini yig'ib
    ulgurmay manba uzilgan — tomoshabin faqat 500 ko'rgan.
    """
    monkeypatch.setattr(transport, "_camera", lambda slug: _Baza().row)
    _kadrlar(monkeypatch, tcp=28, udp=188)
    assert transport.check("kam_1") == "udp"
    assert qolda["udp"] is True


def test_ozgina_farq_almashtirmaydi(monkeypatch, qolda):
    """Ikkalasi ham normal ishlayotganda kichik farq sabab bo'lolmaydi —
    aks holda flot sekin-asta butunlay UDP'ga ko'chib ketardi."""
    monkeypatch.setattr(transport, "_camera", lambda slug: _Baza().row)
    _kadrlar(monkeypatch, tcp=150, udp=200)
    assert transport.check("kam_1") is None
    assert qolda == {}


def test_buzuq_kadr_beradigan_transportga_otilmaydi(monkeypatch, qolda):
    """Regressiya: ko'p kadr bersa ham, buzuq beradigan transport yaramaydi.

    Ishlab chiqarishda aynan shu bo'ldi: sekin kanaldagi kameralar
    UDP'ga o'tkazildi va tomoshabin qotish o'rniga BUZUQ tasvir ko'rdi —
    yashil bloklar, surilgan kadrlar.

    O'lchov (10.30.33.57, bir xil 6 soniyalik video):
        TCP : 147 kadr, dekod xatosi 0
        UDP : 136 kadr, dekod xatosi 4

    UDP real vaqtda ko'proq kadr "beradi" (kutmaydi), lekin bir qismi
    yaroqsiz. Tomoshabin uchun buzuq kadr kadr emas.
    """
    monkeypatch.setattr(transport, "_camera", lambda slug: _Baza().row)
    # UDP besh barobar ko'p kadr, lekin buzuq — baribir o'tilmaydi.
    _kadrlar(monkeypatch, tcp=100, udp=500, tcp_buzuq=0, udp_buzuq=4)
    assert transport.check("kam_1") is None
    assert qolda == {}


def test_buzuqlik_kamaysa_otiladi(monkeypatch, qolda):
    """Teskarisi: hozirgi transport buzuq bersa, tozasiga o'tiladi."""
    monkeypatch.setattr(transport, "_camera",
                        lambda slug: _Baza(rtsp_udp=1).row)
    # Hozirgi UDP buzuq, TCP toza va yetarlicha ko'p kadr beradi.
    _kadrlar(monkeypatch, tcp=500, udp=100, tcp_buzuq=0, udp_buzuq=9)
    assert transport.check("kam_1") == "tcp"
    assert qolda["udp"] is False


def test_buzuq_hozirgidan_toza_boshqasiga_nisbatga_qaramay_otiladi(
        monkeypatch, qolda):
    """Regressiya: nisbat qoidasi buzuq transportni ushlab qolardi.

    O'lchovda chiqqan holat:

        3395_km_3395_2_km   UDP  90 kadr, 340 buzuq   (hozirgi)
                            TCP 135 kadr,   0 buzuq

    TCP har jihatdan yaxshi, lekin "3 barobar ko'p kadr" shartiga
    tushmaydi — eski mantiqda kamera buzuq tasvirda qolib ketardi.
    """
    monkeypatch.setattr(transport, "_camera",
                        lambda slug: _Baza(rtsp_udp=1).row)
    _kadrlar(monkeypatch, tcp=135, udp=90, tcp_buzuq=0, udp_buzuq=340)
    assert transport.check("kam_1") == "tcp"
    assert qolda["udp"] is False


def test_toza_boshqasi_kadr_bermasa_otilmaydi(monkeypatch, qolda):
    """Buzuq bo'lsa ham, ALMASHTIRADIGAN narsa bo'lmasa joyida qoladi.

    Qora ekran buzuq tasvirdan yaxshi emas — kamerani umuman
    ko'rsatmaslikdan ko'ra buzuq ko'rsatgan afzal.
    """
    monkeypatch.setattr(transport, "_camera",
                        lambda slug: _Baza(rtsp_udp=1).row)
    _kadrlar(monkeypatch, tcp=0, udp=300, tcp_buzuq=0, udp_buzuq=90)
    assert transport.check("kam_1") is None
    assert qolda == {}


def test_hevc_buzuqligi_ham_sanaladi():
    """Regressiya: naqsh faqat H.264 xabarlarini bilardi.

    H.265 dekoderi butunlay boshqa so'zlar bilan shikoyat qiladi.
    Natijada H.265 kameralarda sinov buzuqlikni UMUMAN ko'rmasdi: UDP
    "toza" chiqar, kamera UDP'ga o'tkazilar, tomoshabin esa BUTUNLAY
    YASHIL ekran ko'rardi (shikastlangan HEVC oqimida dekoder bo'sh
    kadr chiqaradi).

    Xabarlar ishlab chiqarish jurnalidan olingan.
    """
    jurnal = (
        "[hevc @ 0x1] Could not find ref with POC 7\n"
        "[hevc @ 0x1] Skipping invalid undecodable NALU: 39\n"
        "[hevc @ 0x1] The cu_qp_delta -37 is outside the valid range.\n"
        "[hevc @ 0x1] Error constructing the frame RPS.\n"
        "    Last message repeated 10 times\n"
        "frame=  100 fps=25\n"
    )
    # To'rt xabar + takrorlangan o'ni.
    assert transport._buzuq_soni(jurnal) == 14


def test_sog_lom_jurnalda_buzuqlik_topilmaydi():
    """Yolg'on ishga tushish qimmat: toza transport rad etilib qolardi."""
    jurnal = ("frame=   25 fps=0.0 q=-0.0 size=N/A time=00:00:00.96\n"
              "frame=  200 fps= 25 q=-0.0 size=N/A time=00:00:08.00\n")
    assert transport._buzuq_soni(jurnal) == 0

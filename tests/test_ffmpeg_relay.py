"""Kamerani FFmpeg orqali tortish (relay) — yo'l tanlash mantig'i.

Nima uchun bu rejim bor: kamera RTSP sessiyasini `Session: ...;timeout=60`
bilan e'lon qiladi va shu muddat ichida keepalive kutadi. MediaMTX
keepalive yubormaydi, shuning uchun kamera har 60 soniyada ulanishni RST
bilan uzadi — serverda tcpdump bilan o'lchandi. FFmpeg keepalive
yuboradi (o'lchov: shu kamerani ffmpeg 302 s uzilmasdan o'qidi,
MediaMTX 54 soniyada uzildi).

Bu testlar rejimning YOQILISHI va ISTISNOLARI to'g'ri ishlashini
qo'riqlaydi — chunki xato tomonga og'sa butun flot bir zumda o'zgaradi.
"""
import pytest

from media import sync

KAM = {"slug": "kam_1", "ip": "10.0.0.1", "port": 554, "rtsp_path": "/s1",
       "username": "u", "password": "p", "always_on": False}


@pytest.fixture
def yoq(monkeypatch):
    """Relay yoqilgan holat."""
    monkeypatch.setattr(sync, "RTSP_VIA_FFMPEG", True)
    monkeypatch.setattr(sync, "FFMPEG_EXCLUDE", set())


def test_ochirilgan_holda_mediamtx_ozi_tortadi():
    conf = sync.source_path(KAM)
    assert "source" in conf and "runOnDemand" not in conf


def test_yoqilganda_ffmpeg_tortadi(yoq):
    conf = sync.source_path(KAM)
    assert "runOnDemand" in conf
    # Parol konfiguratsiyaga TUSHMASLIGI shart — launcher bazadan oladi.
    assert "source" not in conf
    assert "p@" not in conf["runOnDemand"]
    assert conf["runOnDemandRestart"] is True


def test_istisno_eski_yolda_qoladi(yoq, monkeypatch):
    monkeypatch.setattr(sync, "FFMPEG_EXCLUDE", {"kam_1"})
    assert "source" in sync.source_path(KAM)
    # boshqa kamera esa relay'da
    assert "runOnDemand" in sync.source_path({**KAM, "slug": "kam_2"})


def test_sub_oqim_ham_relay(yoq):
    sub = sync.sub_variant({**KAM, "sub_path": "/s2"})
    conf = sync.source_path(sub)
    assert "runOnDemand" in conf
    assert conf["runOnDemand"].endswith("kam_1_sub")


def test_desired_paths_relay_bilan_ishlaydi(yoq):
    cams = [{**KAM, "enabled": 1, "sub_path": "/s2"}]
    yollar = sync.desired_paths(cams)
    # yo'l ro'yxatda bo'lishi uchun issiq/managed bo'lishi kerak
    sync.mark_warm("kam_1")
    yollar = sync.desired_paths(cams)
    assert "runOnDemand" in yollar["kam_1"]


def test_ffmpeg_only_tanlab_yoqiladi(monkeypatch):
    """Relay HAMMAGA emas, faqat kerakli kameralarga yoqilishi kerak.

    O'lchov (10.30.11.65, bir xil mashina, bir xil MediaMTX): MediaMTX
    o'zi tortganda 180 s da 0 uzilish va bo'sh sekundlar 5 %, relay
    orqali esa 51 %. Ya'ni relay standart yo'l bo'lolmaydi — u faqat
    keepalive kutadigan registratorlar uchun.
    """
    import importlib

    from media import sync

    monkeypatch.setenv("FFMPEG_ONLY", "kam_a, kam_b")
    monkeypatch.delenv("RTSP_VIA_FFMPEG", raising=False)
    monkeypatch.delenv("FFMPEG_EXCLUDE", raising=False)
    importlib.reload(sync)
    try:
        assert sync.pull_via_ffmpeg("kam_a") is True
        assert sync.pull_via_ffmpeg("kam_b") is True
        assert sync.pull_via_ffmpeg("kam_c") is False   # qolgani to'g'ridan
    finally:
        monkeypatch.undo()
        importlib.reload(sync)


def test_ffmpeg_only_global_bayroqdan_ustun(monkeypatch):
    """`FFMPEG_ONLY` dagi kamera istisno ro'yxatida bo'lsa ham relay orqali.

    Ikkovi bir vaqtda yozilishi chalkashlik, lekin natija aniq bo'lsin:
    aniq nomlab ko'rsatilgan qaror umumiy istisnodan kuchliroq.
    """
    import importlib

    from media import sync

    monkeypatch.setenv("FFMPEG_ONLY", "kam_a")
    monkeypatch.setenv("FFMPEG_EXCLUDE", "kam_a")
    monkeypatch.setenv("RTSP_VIA_FFMPEG", "1")
    importlib.reload(sync)
    try:
        assert sync.pull_via_ffmpeg("kam_a") is True
    finally:
        monkeypatch.undo()
        importlib.reload(sync)

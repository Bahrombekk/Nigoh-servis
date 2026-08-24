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

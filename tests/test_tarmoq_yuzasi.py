"""Media portlari qaysi interfeysda eshitadi va oqimni kim joylay oladi.

Standart holda hammasi `:port` edi — server tashqi IP'ga ega bo'lsa
RTSP/HLS/WebRTC internetdan ko'rinardi. Chipta himoya qiladi, lekin
hujum yuzasini bekorga ochib turishning ma'nosi yo'q.
"""
import importlib

import yaml

from media import sync


def _konf(monkeypatch, **muhit):
    for k, v in muhit.items():
        monkeypatch.setenv(k, v)
    importlib.reload(sync)
    return yaml.safe_load(sync.build_config([]))


def test_rtsp_har_doim_lokal(monkeypatch):
    """RTSP'ga faqat lokal jarayonlar ulanadi — launcher va o'girish."""
    c = _konf(monkeypatch, MEDIA_BASE="")
    assert c["rtspAddress"].startswith("127.0.0.1:")


def test_rtsp_atayin_ochilishi_mumkin(monkeypatch):
    """Boshqa tizim Nigoh'dan RTSP tortsa — ataylab ochiladi."""
    c = _konf(monkeypatch, RTSP_PUBLIC="1")
    assert c["rtspAddress"].startswith(":")


def test_proksi_ortida_media_portlari_yopiladi(monkeypatch):
    c = _konf(monkeypatch, MEDIA_BASE="https://kamera.example.uz/media",
              RTSP_PUBLIC="0")
    assert c["hlsAddress"].startswith("127.0.0.1:")
    assert c["webrtcAddress"].startswith("127.0.0.1:")


def test_ice_porti_hech_qachon_yopilmaydi(monkeypatch):
    """Regressiya: ICE tomoshabin bilan TO'G'RIDAN gaplashadi.

    U ham lokalga bog'lansa WebRTC umuman ulanmaydi va har tomoshabin
    jimgina HLS'ga tushadi — bu eng qimmat jim nosozliklardan biri.
    """
    c = _konf(monkeypatch, MEDIA_BASE="https://kamera.example.uz/media")
    assert not c["webrtcLocalUDPAddress"].startswith("127.0.0.1")


def test_proksisiz_ornatmada_portlar_ochiq_qoladi(monkeypatch):
    """MEDIA_BASE yo'q — tomoshabin to'g'ridan keladi, yopib bo'lmaydi."""
    monkeypatch.delenv("MEDIA_BASE", raising=False)
    c = _konf(monkeypatch, RTSP_PUBLIC="0")
    assert c["hlsAddress"].startswith(":")
    assert c["webrtcAddress"].startswith(":")


def test_origin_domen_bilan_cheklanadi(monkeypatch):
    c = _konf(monkeypatch, MEDIA_BASE="https://kamera.example.uz/media")
    assert c["hlsAllowOrigins"] == ["https://kamera.example.uz"]
    assert c["webrtcAllowOrigins"] == ["https://kamera.example.uz"]


def test_origin_qolda_beriladi(monkeypatch):
    c = _konf(monkeypatch, ALLOW_ORIGINS="https://a.uz, https://b.uz")
    assert c["hlsAllowOrigins"] == ["https://a.uz", "https://b.uz"]


def test_origin_malumot_yoqligida_ochiq_qoladi(monkeypatch):
    """Bilinmaganda cheklamaymiz — aks holda tomosha umuman buziladi."""
    monkeypatch.delenv("MEDIA_BASE", raising=False)
    monkeypatch.delenv("ALLOW_ORIGINS", raising=False)
    c = _konf(monkeypatch)
    assert c["hlsAllowOrigins"] == ["*"]


def test_uzoq_tugun_portlari_ochiq(monkeypatch):
    """Uzoq tugunga tomoshabin TO'G'RIDAN ulanadi, nginx oralig'ida yo'q."""
    monkeypatch.setenv("MEDIA_BASE", "https://kamera.example.uz/media")
    importlib.reload(sync)
    c = yaml.safe_load(sync.build_config(
        [], node={"public_host": "node2.example.uz",
                  "api_base": "http://10.0.0.5:9997"}))
    assert c["hlsAddress"].startswith(":")
    assert c["webrtcAddress"].startswith(":")

"""Nginx `auth_request` uchun HLS chipta tekshiruvi.

`hlsCDNSecret` yoqilganda MediaMTX Bearer sarlavhasi bilan kelgan
so'rovni shartsiz o'tkazadi va bizning POST /auth/stream ilgagimizni
chaqirmaydi. Sarlavhani nginx qo'yadi, ya'ni chiptani nginx tekshirishi
shart — bu testlar aynan o'sha tekshiruvni qo'riqlaydi. U buzilsa
slug'ni bilgan har kim kamerani ko'ra olardi.
"""
import pytest
from fastapi.testclient import TestClient

from api import create_app
from core import security


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


def _sora(client, uri, ip="203.0.113.10"):
    return client.get("/api/auth/hls",
                      headers={"X-Original-URI": uri, "X-Viewer-IP": ip})


def test_chiptasiz_rad_etiladi(client):
    r = _sora(client, "/media/hls/kam_a/index.m3u8")
    assert r.status_code == 401


def test_togri_chipta_otadi(client):
    tok = security.stream_token("kam_a")
    r = _sora(client, f"/media/hls/kam_a/index.m3u8?token={tok}")
    assert r.status_code == 204


def test_soxta_chipta_rad_etiladi(client):
    # IP alohida: bir xil IP dan avval to'g'ri chipta kelgan bo'lsa
    # sessiya ochiladi va soxta chipta ham o'tib ketardi (bu to'g'ri
    # xatti-harakat, lekin bu yerda tekshiriladigan narsa emas).
    r = _sora(client, "/media/hls/kam_a/index.m3u8?token=999999999.soxta",
              ip="203.0.113.50")
    assert r.status_code == 401


def test_boshqa_oqimga_otmaydi(client):
    """`kam_a` chiptasi `kam_b` ni ochmasligi kerak."""
    tok = security.stream_token("kam_a")
    r = _sora(client, f"/media/hls/kam_b/index.m3u8?token={tok}", ip="203.0.113.11")
    assert r.status_code == 401


def test_chiptasiz_segmentlar_sessiya_orqali_otadi(client):
    """Bearer rejimida MediaMTX bola pleylisti va segment manzillariga
    hech qanday parametr qo'shmaydi — ular chiptasiz keladi va faqat
    (ip, yo'l) sessiyasi orqali o'tishi kerak."""
    ip = "203.0.113.12"
    tok = security.stream_token("kam_c")
    assert _sora(client, f"/media/hls/kam_c/index.m3u8?token={tok}", ip).status_code == 204
    # endi parametrsiz ichki resurslar
    assert _sora(client, "/media/hls/kam_c/video1_stream.m3u8", ip).status_code == 204
    assert _sora(client, "/media/hls/kam_c/video1_seg7.mp4", ip).status_code == 204
    # boshqa IP sessiyaga kirmaydi
    assert _sora(client, "/media/hls/kam_c/video1_seg7.mp4", "203.0.113.99").status_code == 401


def test_prefikssiz_manzil_ham_ishlaydi(client):
    """Nginx boshqa prefiks bilan sozlangan bo'lsa ham buzilmasin."""
    tok = security.stream_token("kam_d")
    r = _sora(client, f"/kam_d/index.m3u8?token={tok}", ip="203.0.113.13")
    assert r.status_code == 204


# ---------- CDN kaliti ----------
#
# Kalit bo'sh bo'lsa MediaMTX sessiyali rejimga tushadi va manba har
# uzilganda tomoshabin doimiy 401 oladi. Ilgari u .env dagi ixtiyoriy
# sozlama edi va serverda qo'yilmay qolgandi — shu testlar shu holatning
# qaytishiga yo'l qo'ymaydi.

def test_cdn_kaliti_hech_qachon_bosh_emas():
    kalit = security.hls_cdn_secret()
    assert kalit
    assert len(kalit) >= 32


def test_cdn_kaliti_ozgarmaydi():
    """Kalit har chaqiruvda bir xil — aks holda nginx'dagi nusxasi
    darhol eskirardi va HAMMA HLS so'rovi 401 bo'lardi."""
    assert security.hls_cdn_secret() == security.hls_cdn_secret()


def test_mediamtx_konfiguratsiyasida_kalit_bor():
    import yaml

    from media import sync
    conf = yaml.safe_load(sync.build_config([]))
    assert conf["hlsCDNSecret"] == security.hls_cdn_secret()


def test_muhit_ozgaruvchisi_ustun(monkeypatch):
    monkeypatch.setenv("HLS_CDN_SECRET", "qolda-qoyilgan-kalit")
    assert security.hls_cdn_secret() == "qolda-qoyilgan-kalit"


def test_sessiyali_rejim_jurnalga_tushadi(client, monkeypatch):
    """Manzilda `session=` bo'lsa — nginx Bearer qo'ymayapti. Bu jimgina
    o'tib ketmasligi kerak: aynan shu nosozlik ishlab chiqarishda
    haftalab sezilmay turgandi."""
    from api import auth as auth_modul
    yozuvlar = []
    monkeypatch.setattr(auth_modul, "log",
                        lambda *a, **k: yozuvlar.append((a, k)))
    monkeypatch.setattr(auth_modul, "_bearer_warned", [0.0])
    tok = security.stream_token("kam_e")
    _sora(client, f"/media/hls/kam_e/video1_stream.m3u8?session=abc&token={tok}",
          ip="203.0.113.20")
    assert any(a[1] == "hls_bearer_yoq" for a, _ in yozuvlar)

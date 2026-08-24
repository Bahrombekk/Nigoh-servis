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

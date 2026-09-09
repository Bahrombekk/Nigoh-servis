"""Kirish (login) brute-force himoyasi.

Ikkita xossani qulflaydi:

  * cheklov AYLANIB O'TILMAYDI — `X-Forwarded-For` faqat ishonchli
    proksidan hisobga olinadi, aks holda hujumchi har so'rovda soxta
    sarlavha yuborib har safar yangi hisob ochib olardi;
  * cheklov SERVERNI TO'XTATMAYDI — kutish mijozga aytiladi (429 +
    Retry-After), so'rov ichida uxlanmaydi. Ilgari bu yerda 30 s gacha
    `time.sleep` bor edi va 40 ta parallel urinish Starlette'ning
    threadpool'ini to'ldirib butun API'ni javobsiz qoldirardi.
"""
import time

import pytest
from fastapi.testclient import TestClient

import api
from api import auth, create_app


@pytest.fixture(autouse=True)
def _toza_hisob():
    """Har test toza hisob bilan boshlansin — `_fails` modul darajasida."""
    auth._fails.clear()
    yield
    auth._fails.clear()


@pytest.fixture(scope="module")
def ui_client(request):
    """Login endpointi faqat ENABLE_UI=1 da ro'yxatga olinadi."""
    eski = api.ENABLE_UI
    api.ENABLE_UI = True
    with TestClient(create_app()) as c:
        yield c
    api.ENABLE_UI = eski


# ---------- ishonchli proksi ----------

def test_soxta_forwarded_for_hisobga_olinmaydi():
    """Internetdan to'g'ridan kelgan so'rovning sarlavhasi — hujumchining
    so'zi, unga ishonilmaydi."""
    class Req:
        def __init__(self, peer, fwd):
            self.client = type("C", (), {"host": peer})()
            self.headers = {"x-forwarded-for": fwd}

    # Ommaviy manzildan kelgan: sarlavha e'tiborsiz, hisob peer bo'yicha.
    assert auth._client_ip(Req("8.8.8.8", "1.2.3.4")) == "8.8.8.8"
    # Nginx (loopback) ortidan kelgan: haqiqiy tomoshabin sarlavhada.
    assert auth._client_ip(Req("127.0.0.1", "9.9.9.9")) == "9.9.9.9"
    # Zanjir bo'lsa birinchisi — eng tashqi mijoz.
    assert auth._client_ip(Req("127.0.0.1", "9.9.9.9, 10.0.0.5")) == "9.9.9.9"


def test_ishonchli_proksi_toifalari():
    assert auth._ishonchli_proksi("127.0.0.1")
    assert auth._ishonchli_proksi("10.0.0.5")        # ichki tarmoq
    assert not auth._ishonchli_proksi("8.8.8.8")      # ommaviy
    assert not auth._ishonchli_proksi("testclient")   # ip emas


# ---------- kutish egri chizig'i ----------

def test_dastlabki_urinishlar_jazosiz():
    for _ in range(auth._FAIL_FREE):
        assert auth._retry_after("ip1") == 0.0
        auth._note_fail("ip1")
    assert auth._retry_after("ip1") > 0.0


def test_kutish_ikki_baravar_osadi_va_chegaralanadi():
    for _ in range(auth._FAIL_FREE):
        auth._note_fail("ip2")
    auth._note_fail("ip2")                     # 6-xato -> ~2 s
    ikki = auth._retry_after("ip2")
    for _ in range(20):                        # ko'p xato -> shift
        auth._note_fail("ip2")
    assert ikki <= auth._retry_after("ip2") <= auth._FAIL_MAX_DELAY


def test_togri_parol_hisobni_tozalaydi():
    for _ in range(auth._FAIL_FREE + 2):
        auth._note_fail("ip3")
    assert auth._retry_after("ip3") > 0.0
    auth._clear_fails("ip3")
    assert auth._retry_after("ip3") == 0.0


# ---------- endpoint xatti-harakati ----------

def test_kop_urinishdan_keyin_429_va_retry_after(ui_client):
    xato = {"username": "yoq-bunday-odam", "password": "xato"}
    for _ in range(auth._FAIL_FREE):
        assert ui_client.post("/api/v1/auth/login", json=xato).status_code == 401

    boshlandi = time.monotonic()
    r = ui_client.post("/api/v1/auth/login", json=xato)
    ketgan = time.monotonic() - boshlandi

    assert r.status_code == 429
    assert int(r.headers["Retry-After"]) >= 1
    # Eng muhimi: javob DARHOL keldi — so'rov ichida uxlanmadi.
    assert ketgan < 1.0, f"so'rov {ketgan:.1f} s ushlab turildi — uxlash qaytibdi"


def test_togri_parol_bilan_kirish_ishlaydi(ui_client):
    """Cheklov qayta yozilgandan keyin ham oddiy kirish buzilmasin.

    Cookie'ning `secure` bayrog'i http'da QO'YILMASLIGI kerak — aks holda
    brauzer uni saqlamaydi va lokal debug UI umuman kira olmaydi.
    """
    from core import security
    from core.db import get_db

    with get_db() as db:
        security.set_password(db, "sinov-admin", "SinovParol123")

    r = ui_client.post("/api/v1/auth/login",
                       json={"username": "sinov-admin", "password": "SinovParol123"})
    assert r.status_code == 200, r.text
    assert r.json()["username"] == "sinov-admin"

    cookie = r.headers["set-cookie"]
    assert security.SESSION_COOKIE in cookie
    assert "HttpOnly" in cookie
    assert "secure" not in cookie.lower(), "http'da secure qo'yilsa UI kira olmaydi"

    assert ui_client.get("/api/v1/auth/me").json()["authenticated"] is True


def test_cheklangan_urinish_hisobni_oshirmaydi(ui_client):
    """Aks holda tinmay urinayotgan hujumchi shu ip'ni cheksiz qulflardi —
    haqiqiy foydalanuvchi hech qachon kira olmasdi."""
    xato = {"username": "yoq-bunday-odam", "password": "xato"}
    for _ in range(auth._FAIL_FREE):
        ui_client.post("/api/v1/auth/login", json=xato)

    birinchi = auth._retry_after("testclient")
    for _ in range(10):                        # 429 oladigan urinishlar
        assert ui_client.post("/api/v1/auth/login", json=xato).status_code == 429
    assert auth._retry_after("testclient") <= birinchi

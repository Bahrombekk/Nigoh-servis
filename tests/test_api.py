"""API testlari — FastAPI TestClient bilan (haqiqiy HTTP qatlami).

MediaMTX va kameralar kerak emas: manual (tayyor oqim) kameralar
ishlatiladi. Kalit conftest'da: X-API-Key: test-kalit.
"""
import pytest
from fastapi.testclient import TestClient

from api import create_app
from core.db import get_db

KEY = {"X-API-Key": "test-kalit"}


@pytest.fixture(scope="module")
def client():
    with TestClient(create_app()) as c:
        yield c


def _yangi(client, nom, ext=""):
    r = client.post("/api/v1/admin/cameras", headers=KEY, json={
        "name": nom, "region": "Sinovobod", "source_type": "manual",
        "stream_url": f"https://misol.uz/{nom}.m3u8", "external_id": ext,
    })
    assert r.status_code == 201, r.text
    return r.json()


def test_kalitsiz_401(client):
    for path in ("/api/v1/cameras", "/api/v1/cameras/status?all=1",
                 "/api/v1/admin/status", "/api/v1/events", "/api/v1/vendors"):
        assert client.get(path).status_code == 401, path
    assert client.post("/api/v1/streams",
                       json={"ids": [1]}).status_code == 401


def test_notogri_kalit_401(client):
    r = client.get("/api/v1/cameras", headers={"X-API-Key": "xato"})
    assert r.status_code == 401


def test_ui_ochiq_login_yoq(client):
    # ENABLE_UI=0 — login yuzasi umuman ro'yxatda yo'q
    assert client.post("/api/v1/auth/login",
                       json={"username": "a", "password": "b"}).status_code == 404
    # ildiz servis tanishtiruvi qaytaradi
    r = client.get("/")
    assert r.status_code == 200 and r.json()["service"] == "nigoh"


def test_kamera_crud_va_external_id(client):
    _yangi(client, "Ext sinov", ext="api-test-1")
    try:
        # ext: orqali oqim (1.1 qabul mezoni)
        r = client.get("/api/v1/cameras/ext:api-test-1/stream", headers=KEY)
        assert r.status_code == 200
        assert r.json()["stream_url"] == "https://misol.uz/Ext sinov.m3u8"

        # takror external_id -> 409
        r = client.post("/api/v1/admin/cameras", headers=KEY, json={
            "name": "Boshqa", "region": "S", "source_type": "manual",
            "stream_url": "https://x/1.m3u8", "external_id": "api-test-1"})
        assert r.status_code == 409

        # ext: bilan tahrirlash
        r = client.put("/api/v1/admin/cameras/ext:api-test-1", headers=KEY,
                       json={"name": "Ext sinov 2", "region": "Sinovobod",
                             "source_type": "manual",
                             "stream_url": "https://misol.uz/2.m3u8",
                             "external_id": "api-test-1"})
        assert r.status_code == 200 and r.json()["name"] == "Ext sinov 2"
    finally:
        assert client.delete("/api/v1/admin/cameras/ext:api-test-1",
                             headers=KEY).status_code == 204


def test_takror_ip_qoshilmaydi_lekin_201(client):
    """Bir xil IP+port+yo'l qayta yuborilsa: javob yangi qo'shilgandagidek
    (201 + kamera), lekin nusxa yaratilmaydi. Tashqi tizimning dev va prod
    muhitlari bitta Nigoh'ga ulanganda ikkinchisi 409 ga urilmasin.

    Kamera bazaga to'g'ridan yoziladi: takror yo'l probe'gacha qaytadi,
    ya'ni test tarmoqqa chiqmaydi.
    """
    with get_db() as db:
        db.execute(
            "INSERT INTO cameras (name, region, lat, lng, stream_url, slug, "
            "ip, port, rtsp_path, enabled) "
            "VALUES ('Takror', 'TakrorTest', 0, 0, '', 'takror_test', "
            "'10.255.255.9', 554, '/stream1', 1)")

    payload = {"name": "Boshqa nom", "region": "Boshqa", "source_type": "rtsp",
               "ip": "10.255.255.9", "port": 554, "rtsp_path": "/stream1",
               "external_id": "takror-ext-1"}
    try:
        r = client.post("/api/v1/admin/cameras", headers=KEY, json=payload)
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["name"] == "Takror"                 # mavjud kamera qaytdi
        assert body["external_id"] == "takror-ext-1"    # tashqi ID biriktirildi
        assert r.headers.get("X-Nigoh-Existing") == "1"

        # Ikkinchi takror ham xuddi shunday, external_id ham o'zgarmaydi.
        r2 = client.post("/api/v1/admin/cameras", headers=KEY,
                         json={**payload, "external_id": "takror-ext-2"})
        assert r2.status_code == 201
        assert r2.json()["id"] == body["id"]
        assert r2.json()["external_id"] == "takror-ext-1"

        with get_db() as db:
            soni = db.execute("SELECT COUNT(*) FROM cameras WHERE ip = ?",
                              ("10.255.255.9",)).fetchone()[0]
        assert soni == 1                                # nusxa yaratilmadi
    finally:
        with get_db() as db:
            db.execute("DELETE FROM cameras WHERE ip = ?", ("10.255.255.9",))


def test_takror_poyga_paytida_ham_nusxa_yaratilmaydi(client, monkeypatch):
    """Ikki so'rov BIR VAQTDA kelsa ham bitta kamera qoladi.

    Qo'shishdan oldingi tekshiruv o'zi yetmaydi: undan keyin RTSP
    probe'lari bir necha soniya ketadi va shu oraliqda kelgan ikkinchi
    so'rov ham tekshiruvdan o'tib ketardi (ishlab chiqarishda 195
    kameradan 30 tasi shunday ikkilangan). Oxirgi so'z bazada —
    `idx_cameras_rtsp`.

    Poyga shunday takrorlanadi: probe chaqirilgan payt "boshqa so'rov"
    o'sha kamerani bazaga yozib qo'yadi.
    """
    from api import helpers

    haqiqiy = helpers.detect_codec

    def _probe_paytida_boshqasi_qoshadi(cam, password):
        with get_db() as db:
            db.execute(
                "INSERT INTO cameras (name, region, lat, lng, stream_url, "
                "slug, ip, port, rtsp_path, enabled) "
                "VALUES (?, 'PoygaTest', 0, 0, '', 'poyga_test', "
                "'10.255.255.10', 554, '/stream1', 1)", ("Poyga g'olibi",))
        monkeypatch.setattr("api.admin.detect_codec", haqiqiy)
        return "H264", False, "", 0.0

    monkeypatch.setattr("api.admin.detect_codec",
                        _probe_paytida_boshqasi_qoshadi)
    monkeypatch.setattr("api.admin.detect_sub_path", lambda cam, pw: ("", ""))

    try:
        r = client.post("/api/v1/admin/cameras", headers=KEY, json={
            "name": "Kechikkan", "region": "Poyga", "source_type": "rtsp",
            "ip": "10.255.255.10", "port": 554, "rtsp_path": "/stream1",
            "external_id": "poyga-ext"})
        assert r.status_code == 201, r.text
        assert r.headers.get("X-Nigoh-Existing") == "1"
        assert r.json()["name"] == "Poyga g'olibi"      # birinchisi qoldi
        assert r.json()["external_id"] == "poyga-ext"   # ID unga biriktirildi

        with get_db() as db:
            soni = db.execute("SELECT COUNT(*) FROM cameras WHERE ip = ?",
                              ("10.255.255.10",)).fetchone()[0]
        assert soni == 1
    finally:
        with get_db() as db:
            db.execute("DELETE FROM cameras WHERE ip = ?", ("10.255.255.10",))


def test_yol_korsatilmagan_takror_ip_nusxa_yaratmaydi(client, monkeypatch):
    """Faqat IP yuborilsa (RTSP yo'l yo'q) — o'sha IP'dagi kamera qaytadi.

    Tashqi tizim kamerani IP bilan yuboradi, yo'lni Nigoh o'zi topib
    qo'ygan bo'ladi (dahua'da `/cam/realmonitor?...`). Qat'iy
    IP+port+yo'l solishtiruvi bunday so'rovni takror deb tanimasdi va
    o'sha kameraning `/stream1` li, oqim bermaydigan ikkinchi nusxasi
    paydo bo'lardi — konsolda bitta kamera ikkita bo'lib ko'rinardi.
    """
    with get_db() as db:
        db.execute(
            "INSERT INTO cameras (name, region, lat, lng, stream_url, slug, "
            "ip, port, rtsp_path, codec, enabled) "
            "VALUES ('Dahua 1-kanal', 'YolTest', 0, 0, '', 'yol_test', "
            "'10.255.255.11', 554, '/cam/realmonitor?channel=1&subtype=0', "
            "'H264', 1)")
    try:
        r = client.post("/api/v1/admin/cameras", headers=KEY, json={
            "name": "16/9 (10.255.255.11)", "region": "16/9",
            "source_type": "rtsp", "ip": "10.255.255.11", "port": 554,
            "external_id": "yol-ext-1"})           # rtsp_path YUBORILMADI
        assert r.status_code == 201, r.text
        assert r.headers.get("X-Nigoh-Existing") == "1"
        assert r.json()["name"] == "Dahua 1-kanal"
        assert r.json()["external_id"] == "yol-ext-1"

        # Standart `/stream1` ni ATAYLAB yuborish ham xuddi shunday:
        # mijoz kodidagi standart qiymat hech qanday kanalni ko'rsatmaydi.
        r2 = client.post("/api/v1/admin/cameras", headers=KEY, json={
            "name": "Yana o'sha", "region": "16/9", "source_type": "rtsp",
            "ip": "10.255.255.11", "port": 554, "rtsp_path": "/stream1"})
        assert r2.status_code == 201
        assert r2.headers.get("X-Nigoh-Existing") == "1"

        # Boshqa ishlab chiqaruvchi shabloni bilan, lekin O'SHA kanal —
        # baribir bitta kamera (hikvision yozuvi ham 1-kanalni bildiradi).
        r3 = client.post("/api/v1/admin/cameras", headers=KEY, json={
            "name": "Hikvision uslubi", "region": "16/9",
            "source_type": "rtsp", "ip": "10.255.255.11", "port": 554,
            "rtsp_path": "/Streaming/Channels/101"})
        assert r3.status_code == 201
        assert r3.headers.get("X-Nigoh-Existing") == "1"

        # Aniq boshqa kanal esa boshqa kamera — u qo'shiladi.
        monkeypatch.setattr("api.admin.detect_codec",
                            lambda cam, pw: ("H264", False, "", 0.0))
        monkeypatch.setattr("api.admin.detect_sub_path", lambda cam, pw: ("", ""))
        monkeypatch.setattr("api.admin._enrich_new_camera",
                            lambda *a, **k: None)
        r4 = client.post("/api/v1/admin/cameras", headers=KEY, json={
            "name": "2-kanal", "region": "YolTest", "source_type": "rtsp",
            "ip": "10.255.255.11", "port": 554,
            "rtsp_path": "/cam/realmonitor?channel=2&subtype=0"})
        assert r4.status_code == 201 and not r4.headers.get("X-Nigoh-Existing")

        with get_db() as db:
            soni = db.execute("SELECT COUNT(*) FROM cameras WHERE ip = ?",
                              ("10.255.255.11",)).fetchone()[0]
        assert soni == 2                            # 1 ta asl + 1 ta 2-kanal
    finally:
        with get_db() as db:
            db.execute("DELETE FROM cameras WHERE ip = ?", ("10.255.255.11",))


def test_batch_streams(client):
    a = _yangi(client, "Batch A", ext="b-a")
    b = _yangi(client, "Batch B")
    try:
        r = client.post("/api/v1/streams", headers=KEY, json={
            "ids": [a["id"], "ext:b-a", b["id"], 999999]})
        assert r.status_code == 200
        body = r.json()
        s = body["streams"]
        assert s[str(a["id"])]["hls"].endswith("Batch A.m3u8")
        assert s["ext:b-a"]["hls"] == s[str(a["id"])]["hls"]
        assert s["999999"] == {"error": "topilmadi"}
        assert body["egress_estimate_mbps"] > 0
        # 128 dan ortiq id — validatsiya xatosi
        r = client.post("/api/v1/streams", headers=KEY,
                        json={"ids": list(range(1, 200))})
        assert r.status_code == 422
    finally:
        client.delete(f"/api/v1/admin/cameras/{a['id']}", headers=KEY)
        client.delete(f"/api/v1/admin/cameras/{b['id']}", headers=KEY)


def test_batch_status(client):
    cam = _yangi(client, "Holat sinov", ext="st-api")
    try:
        r = client.get(f"/api/v1/cameras/status?ids={cam['id']},ext:st-api,777777",
                       headers=KEY)
        assert r.status_code == 200
        body = r.json()
        assert body["total"] == 2                       # takror + topilmagan
        fields = set(body["cameras"][0])
        assert fields == {"id", "external_id", "state", "codec", "sub_codec",
                          "resolution", "last_seen", "snapshot_at"}
        assert client.get("/api/v1/cameras/status",
                          headers=KEY).status_code == 400
    finally:
        client.delete(f"/api/v1/admin/cameras/{cam['id']}", headers=KEY)


def test_auth_stream_mediamtx(client):
    # MediaMTX nomidan: chiptasiz rad, ichki chipta bilan ruxsat
    r = client.post("/api/v1/auth/stream", json={
        "ip": "127.0.0.1", "action": "read", "path": "x", "query": ""})
    assert r.status_code == 401
    from core import security
    r = client.post("/api/v1/auth/stream", json={
        "ip": "127.0.0.1", "action": "publish", "path": "x_h264",
        "query": f"token={security.internal_token()}"})
    assert r.status_code == 200

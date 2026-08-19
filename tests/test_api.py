"""API testlari — FastAPI TestClient bilan (haqiqiy HTTP qatlami).

MediaMTX va kameralar kerak emas: manual (tayyor oqim) kameralar
ishlatiladi. Kalit conftest'da: X-API-Key: test-kalit.
"""
import pytest
from fastapi.testclient import TestClient

from api import create_app

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

"""Ochilish vaqti o'lchovi: yig'ish, chegaralar va /health ko'rinishi."""
import pytest
from fastapi.testclient import TestClient

from api import create_app
from core import metrics

KEY = {"X-API-Key": "test-kalit"}


@pytest.fixture()
def client():
    metrics.reset()
    with TestClient(create_app()) as c:
        yield c
    metrics.reset()


def test_p50_p95():
    # Eng yaqin tartib statistikasi (interpolatsiyasiz): toq sondagi
    # ro'yxatda p50 aynan o'rtadagi qiymat.
    values = list(range(1, 102))            # 1..101, o'rtasi 51
    assert metrics.percentile(values, 0.5) == 51
    assert metrics.percentile(values, 0.95) == 96
    assert metrics.percentile([7], 0.95) == 7      # bitta namuna
    assert metrics.percentile([], 0.5) == 0        # namuna yo'q — portlamaydi
    # Tartibsiz kelgan namunalar ham to'g'ri saralanadi.
    assert metrics.percentile([900, 5, 100], 0.5) == 100


def test_transport_kesimida_yigiladi(client):
    for total in (100, 200, 300):
        r = client.post("/api/v1/metrics/open", headers=KEY, json={
            "camera_id": 1, "transport": "webrtc", "mode": "direct",
            "stream_ms": 10, "signal_ms": 40, "frame_ms": total - 50,
            "total_ms": total})
        assert r.status_code == 204
    client.post("/api/v1/metrics/open", headers=KEY,
                json={"transport": "hls", "total_ms": 3000})

    body = client.get("/health").json()["open_ms"]
    assert body["webrtc"]["n"] == 3
    assert body["webrtc"]["total_ms"]["p50"] == 200
    assert body["webrtc"]["signal_ms"]["p50"] == 40
    assert body["hls"]["n"] == 1                 # transportlar aralashmaydi


def test_yovvoyi_qiymat_statistikani_buzmaydi(client):
    """Uxlab qolgan yorliq soatlab 'ochilgan' deb yozishi mumkin."""
    client.post("/api/v1/metrics/open", headers=KEY,
                json={"transport": "webrtc", "total_ms": 599_000})
    p50 = client.get("/health").json()["open_ms"]["webrtc"]["total_ms"]["p50"]
    assert p50 == metrics.MAX_MS


def test_notanish_transport_alohida_qopga(client):
    client.post("/api/v1/metrics/open", headers=KEY,
                json={"transport": "kvant-aloqa", "total_ms": 5})
    assert "boshqa" in client.get("/health").json()["open_ms"]


def test_halqa_bufer_chegarasi():
    metrics.reset()
    for i in range(metrics.MAX_SAMPLES + 50):
        metrics.record("webrtc", {"total_ms": i})
    assert metrics.summary()["webrtc"]["n"] == metrics.MAX_SAMPLES
    metrics.reset()


def test_kalitsiz_yozib_bolmaydi(client):
    assert client.post("/api/v1/metrics/open", json={"total_ms": 1}).status_code == 401

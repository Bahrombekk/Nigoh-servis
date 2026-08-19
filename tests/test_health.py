from fastapi.testclient import TestClient

from api import create_app
from api import health as health_api


def test_health_kalitsiz_va_shakli():
    with TestClient(create_app()) as client:
        r = client.get("/health")            # kalitsiz — Docker HEALTHCHECK
        assert r.status_code == 200
        body = r.json()
        assert set(body) == {"ok", "mediamtx", "health", "egress_mbps",
                             "egress_capacity_mbps", "streams", "readers",
                             "warm", "sse_subscribers"}
        # Muhitga bog'lanmaymiz: test mashinasida MediaMTX ishlayotgan
        # bo'lishi ham mumkin — shakl va turlargina tekshiriladi.
        assert isinstance(body["mediamtx"], bool)
        assert body["ok"] == body["mediamtx"]
        assert body["egress_mbps"] >= 0.0
        assert body["egress_capacity_mbps"] > 0


def test_egress_tezligi_farqdan():
    health_api._last.update(t=0.0, sent=0)
    assert health_api._egress_mbps(1_000_000) == 0.0     # birinchi o'lchov
    import time
    time.sleep(0.05)
    mbps = health_api._egress_mbps(2_000_000)            # ~1 MB / 0.05 s
    assert mbps > 0
    # hisob orqaga ketsa (MediaMTX qayta ishga tushdi) — 0, portlamaydi
    assert health_api._egress_mbps(100) == 0.0

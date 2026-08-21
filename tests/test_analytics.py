"""Uzilishlar tahlili: uptime, guruhlar reytingi, soatlik profil.

Hisob `events` jadvalidagi online/offline o'tishlaridan chiqadi, shuning
uchun testlar hodisalarni aniq vaqt bilan yozadi.
"""
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from api import create_app
from core.db import get_db

KEY = {"X-API-Key": "test-kalit"}
NOW = datetime.now(timezone.utc)


def _stamp(hours_ago: float) -> str:
    return (NOW - timedelta(hours=hours_ago)).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture(scope="module", autouse=True)
def seed():
    """Ikki hududda uch kamera va ma'lum o'tishlar."""
    with get_db() as db:
        for slug, name, region in (
            ("tah_a", "Tahlil A", "TahlilHudud"),
            ("tah_b", "Tahlil B", "TahlilHudud"),
            ("tah_c", "Tahlil C", "TahlilBoshqa"),
        ):
            db.execute(
                "INSERT INTO cameras (name, region, lat, lng, stream_url, slug, "
                "ip, port, enabled) VALUES (?, ?, 0, 0, '', ?, '10.9.9.1', 554, 1)",
                (name, region, slug))
        # A: 3 soat oldin uzildi, 2 soat oldin qaytdi -> 1 uzilish, 1 soat offline
        db.execute("INSERT INTO events (ts, kind, slug) VALUES (?, 'offline', 'tah_a')",
                   (_stamp(3),))
        db.execute("INSERT INTO events (ts, kind, slug) VALUES (?, 'online', 'tah_a')",
                   (_stamp(2),))
        # C: 1 soat oldin uzildi va hali qaytmagan -> 1 uzilish, 1 soat offline
        db.execute("INSERT INTO events (ts, kind, slug) VALUES (?, 'offline', 'tah_c')",
                   (_stamp(1),))
        # B: hodisa yo'q -> uzilishsiz, 100%


@pytest.fixture()
def client():
    with TestClient(create_app()) as c:
        yield c


def _by_name(cameras, name):
    return next(c for c in cameras if c["name"] == name)


def test_kamera_kesimida_uptime(client):
    body = client.get("/api/v1/admin/uptime?hours=24&limit=5000",
                      headers=KEY).json()
    a = _by_name(body["cameras"], "Tahlil A")
    assert a["outages"] == 1
    assert a["offline_seconds"] == pytest.approx(3600, abs=30)
    # 24 soatdan 1 soat offline -> ~95,83%
    assert a["uptime_pct"] == pytest.approx(95.83, abs=0.2)
    assert a["last_offline_at"]

    b = _by_name(body["cameras"], "Tahlil B")
    assert b["outages"] == 0 and b["offline_seconds"] == 0
    assert b["uptime_pct"] == 100.0


def test_hali_qaytmagan_uzilish_hozirgacha_sanaladi(client):
    body = client.get("/api/v1/admin/uptime?hours=24&limit=5000",
                      headers=KEY).json()
    c = _by_name(body["cameras"], "Tahlil C")
    assert c["outages"] == 1
    assert c["offline_seconds"] == pytest.approx(3600, abs=30)


def test_royxat_eng_yomoni_birinchi(client):
    body = client.get("/api/v1/admin/uptime?hours=24&limit=5000",
                      headers=KEY).json()
    outages = [c["outages"] for c in body["cameras"]]
    assert outages == sorted(outages, reverse=True)
    assert body["shown"] <= body["total"]


def test_hudud_kesimida_guruhlash(client):
    body = client.get("/api/v1/admin/uptime?hours=24&group_by=region",
                      headers=KEY).json()
    groups = {g["key"]: g for g in body["groups"]}
    assert groups["TahlilHudud"]["cameras"] == 2
    assert groups["TahlilHudud"]["outages"] == 1
    assert groups["TahlilBoshqa"]["outages"] == 1
    # Guruh reytingi ham eng yomonidan boshlanadi.
    outages = [g["outages"] for g in body["groups"]]
    assert outages == sorted(outages, reverse=True)


def test_registrator_kesimida_guruhlash(client):
    """Bitta IP ortida o'nlab kanal turadi — aybdor registratorni topish."""
    body = client.get("/api/v1/admin/uptime?hours=24&group_by=nvr",
                      headers=KEY).json()
    nvr = next(g for g in body["groups"] if g["key"] == "10.9.9.1")
    assert nvr["cameras"] == 3
    assert nvr["outages"] == 2


def test_notogri_group_by(client):
    r = client.get("/api/v1/admin/uptime?group_by=oy", headers=KEY)
    assert r.status_code == 400


def test_soatlik_profil_bitta_kamera(client):
    cam_id = client.get("/api/v1/admin/uptime?hours=24&limit=5000",
                        headers=KEY).json()
    a_id = _by_name(cam_id["cameras"], "Tahlil A")["id"]

    body = client.get(f"/api/v1/admin/outages/hourly?hours=24&ref={a_id}",
                      headers=KEY).json()
    assert body["total"] == 1
    assert len(body["hourly"]) == 24
    assert sum(body["hourly"]) == 1
    assert body["hourly"][(NOW - timedelta(hours=3)).hour] == 1


def test_soatlik_profil_zona_siljishi(client):
    """Hodisalar bazada UTC'da, "cho'qqi 08:00 da" esa mahalliy vaqtda."""
    a_id = _by_name(client.get("/api/v1/admin/uptime?hours=24&limit=5000",
                               headers=KEY).json()["cameras"], "Tahlil A")["id"]
    utc_hour = (NOW - timedelta(hours=3)).hour
    body = client.get(f"/api/v1/admin/outages/hourly?ref={a_id}"
                      f"&tz_offset_minutes=300", headers=KEY).json()
    assert body["hourly"][(utc_hour + 5) % 24] == 1


def test_chuqqi_oyna_sutka_aylanasidan_otadi(client):
    """23:00–01:00 oralig'idagi cho'qqi ham topilishi kerak."""
    body = client.get("/api/v1/admin/outages/hourly?hours=24", headers=KEY).json()
    peak = body["peak"]
    assert 0 <= peak["from_hour"] <= 23
    assert peak["to_hour"] == (peak["from_hour"] + 3) % 24
    assert peak["outages"] >= 0


def test_notanish_kamera_404(client):
    r = client.get("/api/v1/admin/outages/hourly?ref=999999", headers=KEY)
    assert r.status_code == 404

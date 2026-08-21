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


# ---------- bitta kameraning tarixi ----------

def _cam_id(client, name):
    body = client.get("/api/v1/admin/uptime?hours=24&limit=5000", headers=KEY).json()
    return _by_name(body["cameras"], name)["id"]


def test_tarix_kunlik_va_soatlik_bolinadi(client):
    """A: 3 soat oldin uzildi, 2 soat oldin qaytdi."""
    r = client.get(f"/api/v1/admin/cameras/{_cam_id(client, 'Tahlil A')}/history"
                   "?days=30&day=0&tz_offset_minutes=0", headers=KEY)
    assert r.status_code == 200
    d = r.json()
    assert d["camera"]["name"] == "Tahlil A"
    assert len(d["daily"]) == 30
    assert len(d["hourly_offline_seconds"]) == 24
    assert d["daily"][0]["days_back"] == 0          # birinchi element — bugun
    # Uzilish tugagan, shuning uchun jurnalda "tiklandi".
    assert d["outages"] and d["outages"][0]["recovered"] is True
    assert d["outages"][0]["seconds"] == pytest.approx(3600, abs=30)
    assert d["summary"]["mttr_seconds"] == pytest.approx(3600, abs=30)


def test_tugamagan_uzilish_tiklandi_deb_belgilanmaydi(client):
    """C: 1 soat oldin uzildi va hali qaytmagan — jurnalda yolg'on
    "tiklandi" bo'lmasligi kerak."""
    d = client.get(f"/api/v1/admin/cameras/{_cam_id(client, 'Tahlil C')}/history"
                   "?tz_offset_minutes=0", headers=KEY).json()
    assert d["outages"], "davom etayotgan uzilish jurnalga tushishi kerak"
    assert d["outages"][-1]["recovered"] is False
    # Tugamagan uzilish MTTR'ga kirmaydi — aks holda o'rtacha yolg'on bo'lardi.
    assert d["summary"]["mttr_seconds"] == 0


def test_soat_chegarasidan_otgan_uzilish_bolinadi(client):
    """Uzilish soat chegarasini kesib o'tsa vaqti ikkala uyaga ulush
    bo'yicha tushishi kerak — aks holda "soat 17 da 90 daqiqa" chiqadi."""
    d = client.get(f"/api/v1/admin/cameras/{_cam_id(client, 'Tahlil A')}/history"
                   "?tz_offset_minutes=0", headers=KEY).json()
    hourly = d["hourly_offline_seconds"]
    assert max(hourly) <= 3600, "bitta soat uyasi 3600 s dan oshmaydi"
    # A ning uzilishi 1 soat — bugungi uyalarga jami shuncha tushadi.
    assert sum(hourly) == pytest.approx(3600, abs=60)


def test_zona_kun_chegarasini_suradi(client):
    """Kun chegarasi mahalliy vaqtda hisoblanadi — aks holda kunlik
    o'chiq vaqt boshqa sutkaga tushib ketadi."""
    cam = _cam_id(client, "Tahlil A")
    utc = client.get(f"/api/v1/admin/cameras/{cam}/history?tz_offset_minutes=0",
                     headers=KEY).json()
    plus5 = client.get(f"/api/v1/admin/cameras/{cam}/history?tz_offset_minutes=300",
                       headers=KEY).json()
    assert utc["selected_date"] != plus5["selected_date"] or \
        utc["hourly_offline_seconds"] != plus5["hourly_offline_seconds"]


def test_notanish_kamera_tarixi_404(client):
    assert client.get("/api/v1/admin/cameras/999999/history",
                      headers=KEY).status_code == 404


def test_daqiqalik_chiziq_va_kelajak_uyalar(client):
    d = client.get(f"/api/v1/admin/cameras/{_cam_id(client, 'Tahlil A')}/history"
                   "?tz_offset_minutes=0", headers=KEY).json()
    assert d["strip_minutes"] == 15
    assert len(d["strip"]) == 96
    # Bitta uya 15 daqiqadan oshmaydi.
    assert max(d["strip"]) <= 15 * 60
    # A ning uzilishi 1 soat — chiziqqa jami shuncha tushadi.
    assert sum(d["strip"]) == pytest.approx(3600, abs=60)
    # Bugun hali tugamagan: o'tgan uyalar 96 tadan kam bo'lishi kerak,
    # aks holda kelajak "sog'lom" bo'lib ko'rinardi.
    assert 0 < d["strip_elapsed"] <= 96


def test_harakatlar_jurnali_sub_yolni_ham_oladi(client):
    """Oqim muzlashi sub yo'lda qayd etiladi, lekin u ham shu kameraga
    tegishli. LIKE ishlatilmaydi: slug'dagi "_" LIKE uchun joker."""
    with get_db() as db:
        db.execute("INSERT INTO events (ts, kind, slug, detail) "
                   "VALUES (?, 'stalled', 'tah_a_sub', 'oqim muzladi')",
                   (_stamp(1),))
        # Boshqa kameraning yozuvi aralashib ketmasligi kerak.
        db.execute("INSERT INTO events (ts, kind, slug, detail) "
                   "VALUES (?, 'stalled', 'tah_c', 'begona')", (_stamp(1),))
    d = client.get(f"/api/v1/admin/cameras/{_cam_id(client, 'Tahlil A')}/history"
                   "?tz_offset_minutes=0", headers=KEY).json()
    kinds = {a["kind"] for a in d["actions"]}
    paths = {a["path"] for a in d["actions"]}
    assert "stalled" in kinds
    assert "sub" in paths
    assert all(a["detail"] != "begona" for a in d["actions"])


def test_davom_etayotgan_katak_kelajak_deb_hisoblanmaydi(client):
    """Hozirgi 15 daqiqalik katakka tushgan uzilish ekranda ko'rinishi
    kerak — u "kelajak" bo'lib chizilsa ma'lumot yo'qolardi."""
    d = client.get(f"/api/v1/admin/cameras/{_cam_id(client, 'Tahlil C')}/history"
                   "?tz_offset_minutes=0", headers=KEY).json()
    last_filled = max((i for i, v in enumerate(d["strip"]) if v), default=-1)
    assert last_filled < d["strip_elapsed"], \
        "ma'lumoti bor katak o'tganlar ichida bo'lishi kerak"

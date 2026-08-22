"""Bir marta yo'l qo'ygan xatolar — qaytib kelmasin.

Uchalasi ham jimgina noto'g'ri ishlardi: xato bermas, log ham yozmas,
faqat ma'lumot noto'g'ri bo'lardi.
"""
import pytest
from fastapi.testclient import TestClient

from api import create_app
from core import bus, health
from core.db import get_db

KEY = {"X-API-Key": "test-kalit"}


@pytest.fixture()
def client():
    with TestClient(create_app()) as c:
        yield c


# ---------- bbox bilan `total` ----------

def test_bbox_totalni_ham_filtrlaydi(client):
    """Ilgari sanoq bbox qo'shilishidan OLDIN tuzilardi: xaritada
    `total` butun bazani ko'rsatib turardi va sahifalaydigan mijoz
    hech qachon oxiriga yetmasdi."""
    with get_db() as db:
        for i in range(3):
            db.execute(
                "INSERT INTO cameras (name, region, lat, lng, stream_url, "
                "slug, enabled) VALUES (?, 'BboxTest', ?, ?, '', ?, 1)",
                (f"bbox-{i}", 5.0 + i * 0.01, 5.0 + i * 0.01, f"bbox_test_{i}"))

    body = client.get("/api/v1/cameras?bbox=4.9,4.9,5.005,5.005",
                      headers=KEY).json()
    assert body["shown"] == 1                   # faqat bittasi to'rtburchakda
    assert body["total"] == 1                   # sanoq ham o'sha shart bo'yicha

    keng = client.get("/api/v1/cameras?bbox=4.9,4.9,5.1,5.1", headers=KEY).json()
    assert keng["total"] == keng["shown"] == 3


def test_limit_ishlaganda_total_kattaroq_qoladi(client):
    """`total != shown` — mijoz uchun "hammasi kelmadi" belgisi."""
    body = client.get("/api/v1/cameras?bbox=4.9,4.9,5.1,5.1&limit=2",
                      headers=KEY).json()
    assert body["shown"] == 2
    assert body["total"] == 3


# ---------- check_now SSE'ga e'lon qiladi ----------

def test_check_now_holatni_sseda_elon_qiladi(monkeypatch):
    """Yangi qo'shilgan/tahrirlangan kamera holati (_enrich_new_camera shu
    yo'ldan yuradi) SSE abonentlariga yetishi kerak — ilgari faqat bazaga
    yozilardi va asosiy tizim keyingi sweep'gacha (60 s) eskisini ko'rardi.
    """
    with get_db() as db:
        db.execute(
            "INSERT INTO cameras (name, region, lat, lng, stream_url, slug, "
            "ip, port, enabled, external_id) "
            "VALUES ('sse', 'SseTest', 0, 0, '', 'sse_test', "
            "'10.255.255.7', 554, 1, 'ext-sse-1')")

    published = []
    monkeypatch.setattr(bus, "publish",
                        lambda event, data: published.append((event, data)))
    # Tarmoqqa chiqmaymiz: avval "tirik", keyin "o'chiq" deb ko'rsatamiz.
    monkeypatch.setattr(health, "_tcp_ok", lambda pair: True)
    health.check_now("10.255.255.7", 554)
    assert published == []                       # birinchi o'lchov — o'tish emas

    monkeypatch.setattr(health, "_tcp_ok", lambda pair: False)
    health.check_now("10.255.255.7", 554)

    assert len(published) == 1
    event, data = published[0]
    assert event == "state"
    assert data["state"] == "offline"
    assert data["external_id"] == "ext-sse-1"
    assert data["at"]


# ---------- NVR import sub_codec ----------

def test_nvr_import_sub_kodegini_saqlaydi(client, monkeypatch):
    """Devor plitkasi sub oqimni ko'rsatib turib "H265" yorlig'ini
    chizmasin: asosiy oqim H.265, sub esa deyarli doim H.264."""
    def fake_probe(ip, port, path, username, password):
        sub = path.endswith("02")
        return {"ok": True, "stage": "tayyor", "message": "ok",
                "codec": "H264" if sub else "H265",
                "needs_transcode": not sub,
                "resolution": "1920x1080", "fps": 25.0, "audio": False}

    monkeypatch.setattr("api.admin.probe", fake_probe)
    monkeypatch.setattr("api.admin.health.check_now", lambda ip, port: True)
    monkeypatch.setattr("api.admin.devinfo.device_info",
                        lambda ip, u, p: None)

    r = client.post("/api/v1/admin/nvr/import", headers=KEY, json={
        "ip": "10.255.255.8", "username": "admin", "password": "x",
        "vendor": "hikvision", "channels": "1-2", "region": "NvrTest"})
    assert r.status_code == 200
    assert r.json()["created"] == 2

    rows = client.get("/api/v1/admin/cameras?q=NvrTest", headers=KEY).json()
    assert rows["total"] == 2
    for cam in rows["cameras"]:
        assert cam["codec"] == "H265"            # asosiy oqim
        assert cam["sub_path"].endswith("02")
        assert cam["sub_codec"] == "H264"        # ilgari bo'sh qolardi


# ---------- xato parol registratorni bloklab qo'ymasin ----------

def test_skan_xato_parolda_bitta_urinish_bilan_toxtaydi(client, monkeypatch):
    """Hikvision NVR'lari 5 ta xato urinishdan keyin manba IP'ni
    bloklaydi ("illegal login lock", ~30 daqiqa) va o'shanda o'sha
    registratordagi HAMMA kamera offline bo'lib qoladi. Shuning uchun
    parol xato ekani birinchi urinishdayoq aniqlanib, skan to'xtashi
    kerak — ilgari bu yerda 7 ta shablon parallel sinalardi."""
    from api import devices

    tries = []

    def fake_probe(ip, port, path, username, password):
        tries.append(path)
        return {"ok": False, "stage": "parol", "message": "Login yoki parol noto'g'ri",
                "codec": "", "needs_transcode": False,
                "resolution": "", "fps": 0.0, "audio": False}

    monkeypatch.setattr(devices, "probe", fake_probe)
    job = {"created": 0.0, "events": [], "done": False, "ip": "10.9.9.9",
           "port": 554, "username": "admin", "password": "xato", "vendor": ""}
    devices._run_scan(job, "test-job", 64)

    assert len(tries) == 1, f"qurilmaga {len(tries)} urinish ketdi, 1 bo'lishi kerak"
    kinds = [e[0] for e in job["events"]]
    assert kinds == ["error"]
    assert "parol" in job["events"][0][1]["message"].lower()


def test_nvr_import_xato_parolda_kanallarni_tekshirmaydi(client, monkeypatch):
    """Xuddi shu himoya ommaviy importda ham: 64 kanal × 2 oqim = 128
    xato urinish registratorni albatta bloklardi."""
    tries = []

    def fake_probe(ip, port, path, username, password):
        tries.append(path)
        return {"ok": False, "stage": "parol", "message": "Login yoki parol noto'g'ri",
                "codec": "", "needs_transcode": False,
                "resolution": "", "fps": 0.0, "audio": False}

    monkeypatch.setattr("api.admin.probe", fake_probe)
    r = client.post("/api/v1/admin/nvr/import", headers=KEY, json={
        "ip": "10.9.9.9", "username": "admin", "password": "xato",
        "vendor": "hikvision", "channels": "1-64", "region": "Blok"})
    assert r.status_code == 401
    assert len(tries) == 1, f"qurilmaga {len(tries)} urinish ketdi, 1 bo'lishi kerak"


# ---------- oqim ketayotgan kamera "o'chiq" bo'lmasin ----------

def test_oqim_ketayotgan_kamera_offline_deb_belgilanmaydi(monkeypatch):
    """Muammo: kamera jonli ko'rsatib turadi, bir daqiqadan keyin
    yorlig'i "offline" bo'lib qoladi.

    Sabab: TCP tekshiruvi va MediaMTX ikki mustaqil dalil edi va zaifi
    kuchlisini bekor qilardi. Qurilma band, sekin yoki yangi ulanishni
    rad etayotgan bo'lsa TCP yiqiladi — lekin MediaMTX o'sha paytda
    kameradan bayt olib turgan bo'ladi, ya'ni kamera aniq tirik.
    """
    pair = ("10.255.255.11", 554)
    monkeypatch.setattr(health, "_tcp_ok", lambda p: False)
    health.set_streaming_probe(lambda: {pair})
    try:
        fresh = {pair: False}
        rescued = health._rescue_streaming(fresh)
        assert rescued == [pair]
        assert fresh[pair] is True, "oqim ketyapti — kamera tirik hisoblanadi"
    finally:
        health.set_streaming_probe(None)


def test_oqim_yoq_bolsa_offline_qoladi(monkeypatch):
    """Himoya faqat oqim ketayotganda ishlaydi — aks holda haqiqatan
    o'chgan kamera "online" bo'lib turib qolardi."""
    pair = ("10.255.255.12", 554)
    health.set_streaming_probe(lambda: set())
    try:
        fresh = {pair: False}
        assert health._rescue_streaming(fresh) == []
        assert fresh[pair] is False
    finally:
        health.set_streaming_probe(None)


def test_streaming_probe_yiqilsa_sweep_toxtamaydi(monkeypatch):
    """MediaMTX javob bermasa kuzatuv o'z ishini davom ettirishi kerak."""
    def boom():
        raise RuntimeError("mediamtx yiqildi")
    health.set_streaming_probe(boom)
    try:
        fresh = {("10.255.255.13", 554): False}
        assert health._rescue_streaming(fresh) == []
    finally:
        health.set_streaming_probe(None)


def test_tcp_faqat_timeoutdan_keyin_qayta_urinadi(monkeypatch):
    """Rad etilgan ulanish — aniq javob, qayta urinish behuda va sweep'ni
    cho'zadi. Timeout esa noaniq: kamera band bo'lishi ham mumkin."""
    calls = []

    def fake_connect(pair, timeout):
        calls.append(timeout)
        return False, "refused"
    monkeypatch.setattr(health, "_connect", fake_connect)
    assert health._tcp_ok(("10.0.0.1", 554)) is False
    assert calls == [health.TIMEOUT], "rad etilganda qayta urinilmaydi"

    calls.clear()

    def slow_then_ok(pair, timeout):
        calls.append(timeout)
        return (True, "") if timeout == health.RETRY_TIMEOUT else (False, "timeout")
    monkeypatch.setattr(health, "_connect", slow_then_ok)
    assert health._tcp_ok(("10.0.0.2", 554)) is True
    assert calls == [health.TIMEOUT, health.RETRY_TIMEOUT], "timeout'da bir marta qayta urinadi"

"""Begona MediaMTX'ga tegilmasligi.

Nima uchun bu test bor. Bitta mashinada ikkita Nigoh o'rnatmasi bo'lishi
mumkin (eski monolit va yangi mikroservis) va ikkalasining `.env` faylida
`MEDIAMTX_API=http://127.0.0.1:9997` yozilgan bo'lishi mumkin. Shunda
ikkinchi backend birinchisining MediaMTX'iga ulanadi va `push_to_api`
uning barcha yo'llarini "ortiqcha" deb biladi: jurnalda har 30 soniyada
`+3 / ~1 / -43` takrorlanadi, tomoshabin esa uzilib-qotib turadigan
video ko'radi. Sabab hech qayerda ko'rinmaydi.

Egalik `authHTTPAddress` bo'yicha aniqlanadi: MediaMTX ruxsatni AYNAN
o'z backend'idan so'raydi, ya'ni kimga bo'ysunishini o'zi aytib turadi.
"""
from media import reconciler, sync

# Qo'shni o'rnatmaning backend'i. Port ataylab shu o'rnatmanikidan
# boshqa — aks holda test hech narsani tekshirmasdi.
BEGONA = "http://127.0.0.1:18010/api/auth/stream"
assert BEGONA != sync.STREAM_AUTH_URL


def _global(auth_url):
    def fake_api(method, path, payload=None, api_base=None):
        if path.startswith("/v3/config/global/get"):
            return {"authHTTPAddress": auth_url}
        raise AssertionError(f"begona instansiyaga so'rov ketdi: {method} {path}")
    return fake_api


def test_bizning_instansiya_ok(monkeypatch):
    monkeypatch.setattr(sync, "_api", _global(sync.STREAM_AUTH_URL))
    assert sync.api_status() == "ok"
    assert sync.api_available()


def test_begona_instansiya_aniqlanadi(monkeypatch):
    monkeypatch.setattr(sync, "_api", _global(BEGONA))
    assert sync.api_status() == sync.FOREIGN
    assert not sync.api_available()


def test_auth_sozlanmagan_instansiya_bizniki_hisoblanadi(monkeypatch):
    """Bo'sh `authHTTPAddress` — egalikni bilib bo'lmaydi.

    Qo'lda yozilgan yoki eski konfiguratsiyada bu maydon bo'lmasligi
    mumkin. Bunda ilgarigidek ishonamiz: aks holda yangilanishdan keyin
    ishlab turgan o'rnatmalar to'satdan boshqarilmay qolardi.
    """
    monkeypatch.setattr(sync, "_api", _global(""))
    assert sync.api_status() == "ok"


def test_begona_instansiyada_yollar_ochirilmaydi(monkeypatch):
    """Asosiy himoya: `push_to_api` bironta ham so'rov yubormaydi.

    `_global` yordamchisi global konfiguratsiyadan boshqa har qanday
    so'rovda yiqiladi — ya'ni test "o'chirish yuborilmadi" degan zaif
    tasdiqni emas, "umuman tegilmadi" degan kuchli tasdiqni tekshiradi.
    """
    monkeypatch.setattr(sync, "_api", _global(BEGONA))
    res = sync.push_to_api([{"slug": "k1", "ip": "10.0.0.1", "enabled": 1}])
    assert not res["ok"]
    assert res["removed"] == 0 and res["added"] == 0 and res["updated"] == 0
    assert "boshqa o'rnatmaniki" in res["message"]


def test_reconciler_begona_tugunda_mediamtx_kotarmaydi(monkeypatch):
    """Begona instansiyada o'zimiznikini ko'tarish ham xato.

    API porti band, ya'ni yangi jarayon darhol o'lardi va biz uni har
    tsiklda qayta urintirib turardik.
    """
    node = {"id": 1, "name": "Asosiy", "api_base": "http://127.0.0.1:9997"}
    monkeypatch.setattr(reconciler, "_nodes", lambda: [node])
    monkeypatch.setattr(reconciler.sync, "api_status", lambda api: sync.FOREIGN)
    monkeypatch.setattr(reconciler.sync, "_foreign_message", lambda api: "begona")

    spawned = []
    monkeypatch.setattr(reconciler, "_spawn", lambda: spawned.append(1) or True)
    pushed = []
    monkeypatch.setattr(reconciler.sync, "push_to_api",
                        lambda cams, api_base=None: pushed.append(1) or {})

    reconciler._foreign.clear()
    reconciler._foreign_warned.clear()
    try:
        assert reconciler._tick(lambda: [], False) is False
        assert not spawned, "begona instansiyada MediaMTX ko'tarildi"
        assert not pushed, "begona instansiyaga yo'llar yuborildi"
        assert reconciler.foreign_nodes() == 1
    finally:
        reconciler._foreign.clear()
        reconciler._foreign_warned.clear()
        reconciler._reachable.clear()

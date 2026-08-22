from media.sync import (
    SUB_SUFFIX,
    TRANSCODE_SUFFIX,
    _managed,
    _warm,
    desired_paths,
    mark_warm,
    note_managed,
    source_path,
)


def _cam(**extra):
    base = {"slug": "k1", "ip": "10.0.0.2", "port": 554, "rtsp_path": "/s",
            "username": "", "password": "", "enabled": True,
            "transcode": False, "always_on": False, "sub_path": "",
            "node_id": 1}
    base.update(extra)
    return base


def test_oddiy_kamera_doimiy_royxatga_tushmaydi():
    """Har `paths/add` MediaMTX'da to'liq reload — 5000 kamerani oldindan
    ro'yxatga olib bo'lmaydi (2400 yo'lda bitta qo'shish 297 ms). Oddiy
    kamera talab bo'yicha, ensure_path bilan yaratiladi."""
    wanted = desired_paths([_cam()])
    assert "k1" not in wanted
    assert f"~^[a-z0-9_]+{TRANSCODE_SUFFIX}$" in wanted   # shablon — bitta, doim


def test_ishlatilayotgan_yol_doimiy_royxatda_qoladi():
    """ensure_path yo'lni `managed` deb belgilaydi — reconciler uni
    ko'rilayotgan paytda o'chirib yubormasligi kerak."""
    try:
        assert "k1" not in desired_paths([_cam()])
        note_managed("k1")
        wanted = desired_paths([_cam()])
        assert "k1" in wanted
        assert wanted["k1"]["sourceOnDemand"] is True
    finally:
        _managed.clear()


def test_doim_tayyor_kamera_har_doim_royxatda():
    wanted = desired_paths([_cam(always_on=True)])
    assert "k1" in wanted
    assert wanted["k1"]["sourceOnDemand"] is False


def test_besh_ming_kamera_royxatni_shishirmaydi():
    """5000 kamera bo'lsa ham doimiy ro'yxat kichik qoladi — MediaMTX'ga
    yuboriladigan amallar soni kameralar soniga bog'liq emas."""
    cams = [_cam(slug=f"k{i}", sub_path="/s2") for i in range(5000)]
    cams[7]["always_on"] = True
    wanted = desired_paths(cams)
    # shablon + always_on kameraning asosiy va sub yo'li
    assert len(wanted) == 3
    assert "k7" in wanted and "k7" + SUB_SUFFIX in wanted


def test_desired_paths_sub_va_transcode():
    cam = _cam(sub_path="/s2", transcode=True, always_on=True)
    wanted = desired_paths([cam])
    assert "k1" + SUB_SUFFIX in wanted                     # sub yo'l
    assert "k1" + TRANSCODE_SUFFIX in wanted               # doimiy o'girish
    assert wanted["k1"]["sourceOnDemand"] is False         # always_on
    assert "runOnInit" in wanted["k1" + TRANSCODE_SUFFIX]


def test_desired_paths_ochirilgan_va_ipsiz():
    wanted = desired_paths([_cam(enabled=False), _cam(slug="k2", ip="")])
    assert "k1" not in wanted and "k2" not in wanted


def test_uzoq_tugun_transcodesiz():
    cam = _cam(transcode=True, always_on=True)
    wanted = desired_paths([cam], with_transcode=False)
    assert "k1" in wanted
    assert "k1" + TRANSCODE_SUFFIX not in wanted
    assert not any(k.startswith("~") for k in wanted)      # shablon ham yo'q


def test_issiq_toplam_sourceondemandni_ochiradi():
    cam = _cam(slug="k1_sub")
    try:
        assert source_path(cam)["sourceOnDemand"] is True
        assert mark_warm("k1_sub")
        conf = source_path(cam)
        assert conf["sourceOnDemand"] is False
        assert "sourceOnDemandCloseAfter" not in conf
    finally:
        _warm.clear()


def test_korilayotgan_yol_qayta_sozlanmaydi(monkeypatch):
    """Devorda tomosha qilib o'tirganda video uzilardi.

    Sabab zanjiri: sub yo'l ochilganda 10 daqiqaga issiq qilinadi
    (sourceOnDemand: false). 10 daqiqadan keyin issiqlik so'nadi,
    konfiguratsiya o'zgaradi va reconciler PATCH yuboradi. MediaMTX esa
    yo'l konfiguratsiyasi o'zgarganda manbani QAYTA OCHADI — tomoshabin
    uchun bu videoning uzilishi.

    Ko'rilayotgan yo'lga tegilmasligi kerak: o'zgarish yo'qolmaydi,
    yo'l bo'shashi bilan keyingi tsiklda qo'llanadi.
    """
    from media import sync

    cam = _cam(always_on=True)               # doimiy yo'l — wanted ichida
    calls = []

    def fake_api(method, path, payload=None, api_base=None):
        if path.startswith("/v3/config/paths/list"):
            # MediaMTX'dagi holat `wanted` dan farq qiladi -> PATCH kerak
            return {"pageCount": 1, "items": [
                {"name": "k1", "sourceOnDemand": True},
                {"name": f"~^[a-z0-9_]+{TRANSCODE_SUFFIX}$"}]}
        if path.startswith("/v3/paths/list"):
            return {"pageCount": 1, "items": [
                {"name": "k1", "ready": True, "readers": [{"type": "webrtc"}]}]}
        calls.append((method, path))
        return {}

    monkeypatch.setattr(sync, "_api", fake_api)
    res = sync.push_to_api([cam])
    assert res["ok"]
    # Shablon yo'l (regex) PATCH olishi mumkin — uni hech kim ko'rmaydi.
    # Muhimi: TOMOSHABINI BOR kamera yo'liga tegilmasin.
    patched = [c for c in calls if c[0] == "PATCH" and c[1].endswith("/k1")]
    assert not patched, f"ko'rilayotgan yo'lga PATCH yuborildi: {patched}"


def test_bosh_yol_qayta_sozlanaveradi(monkeypatch):
    """Himoya faqat tomoshabini bor yo'lga — aks holda konfiguratsiya
    hech qachon yangilanmay qolardi."""
    from media import sync

    cam = _cam(always_on=True)
    calls = []

    def fake_api(method, path, payload=None, api_base=None):
        if path.startswith("/v3/config/paths/list"):
            return {"pageCount": 1, "items": [
                {"name": "k1", "sourceOnDemand": True},
                {"name": f"~^[a-z0-9_]+{TRANSCODE_SUFFIX}$"}]}
        if path.startswith("/v3/paths/list"):
            return {"pageCount": 1, "items": []}      # hech kim ko'rmayapti
        calls.append((method, path))
        return {}

    monkeypatch.setattr(sync, "_api", fake_api)
    sync.push_to_api([cam])
    assert any(c[0] == "PATCH" and c[1].endswith("/k1") for c in calls),         "bo'sh yo'l yangilanishi kerak"


def test_hls_tomoshabini_baytlar_bilan_aniqlanadi(monkeypatch):
    """HLS tomoshabini MediaMTX'ning `readers` ro'yxatida KO'RINMAYDI —
    har segment alohida HTTP so'rov, doimiy ulanish emas. Ishlab
    chiqarishda o'lchandi: readers=0, bytesSent=1,2 MB.

    Shu sababli "kimdir ko'ryapti" chiqish baytlarining o'sishidan
    aniqlanadi. Aks holda tomosha o'rtasida yo'l qayta sozilib, video
    uzilardi.
    """
    from media import sync
    sync._sent.clear()
    cam = _cam(always_on=True)
    sent = {"v": 1000}
    calls = []

    def fake_api(method, path, payload=None, api_base=None):
        if path.startswith("/v3/config/paths/list"):
            return {"pageCount": 1, "items": [
                {"name": "k1", "sourceOnDemand": True},
                {"name": f"~^[a-z0-9_]+{TRANSCODE_SUFFIX}$"}]}
        if path.startswith("/v3/paths/list"):
            return {"pageCount": 1, "items": [
                {"name": "k1", "ready": True, "readers": [],
                 "bytesSent": sent["v"]}]}
        calls.append((method, path))
        return {}

    monkeypatch.setattr(sync, "_api", fake_api)
    try:
        # 1-tsikl: taqqoslash uchun tarix yo'q -> hali "ko'rilyapti" emas
        sync.push_to_api([cam])
        calls.clear()
        # 2-tsikl: baytlar o'sdi -> kimdir ko'ryapti -> tegilmaydi
        sent["v"] = 250000
        sync.push_to_api([cam])
        assert not [c for c in calls if c[0] == "PATCH" and c[1].endswith("/k1")], \
            "ko'rilayotgan yo'l qayta sozildi — video uzilardi"
        # 3-tsikl: baytlar qimirlamadi -> hech kim ko'rmayapti -> yangilanadi
        calls.clear()
        sync.push_to_api([cam])
        assert [c for c in calls if c[0] == "PATCH" and c[1].endswith("/k1")], \
            "bo'sh yo'l yangilanishi kerak"
    finally:
        sync._sent.clear()


def test_korilayotgan_sub_yol_issiq_bolib_qoladi(monkeypatch):
    """Issiqlik 10 daqiqada so'nadi. Tomosha davom etayotgan bo'lsa u
    yangilanishi kerak, aks holda sourceOnDemand qayta yoqilib manba
    qayta ochiladi."""
    from media import sync
    sync._sent.clear(); sync._warm.clear()
    cam = _cam(sub_path="/s2", always_on=True)
    sent = {"v": 500}

    def fake_api(method, path, payload=None, api_base=None):
        if path.startswith("/v3/config/paths/list"):
            return {"pageCount": 1, "items": []}
        if path.startswith("/v3/paths/list"):
            return {"pageCount": 1, "items": [
                {"name": "k1" + SUB_SUFFIX, "ready": True, "readers": [],
                 "bytesSent": sent["v"]}]}
        return {}

    monkeypatch.setattr(sync, "_api", fake_api)
    try:
        sync.push_to_api([cam])
        assert not sync.is_warm("k1" + SUB_SUFFIX)
        sent["v"] = 90000                      # tomosha davom etyapti
        sync.push_to_api([cam])
        assert sync.is_warm("k1" + SUB_SUFFIX), "ko'rilayotgan sub yo'l issiq qolishi kerak"
    finally:
        sync._sent.clear(); sync._warm.clear()


def test_korilayotgan_asosiy_yol_ham_issiq_boladi():
    """O'lchov bilan tasdiqlangan sabab: sourceOnDemand rejimida MediaMTX
    manbani ochib-yopib turadi va tomosha shunda uziladi.

      * qurilmadan to'g'ridan tortish      -> 92/90 soniya toza
      * MediaMTX orqali, always_on bilan   -> 90 soniya toza
      * MediaMTX orqali, sourceOnDemand    -> muzlaydi

    Ya'ni asosiy oqim ham ko'rilayotganda issiq bo'lishi shart.
    """
    from media.sync import WARM_MAIN_TTL, is_warm
    cam = _cam()
    try:
        assert source_path(cam)["sourceOnDemand"] is True
        assert mark_warm("k1", WARM_MAIN_TTL)
        assert is_warm("k1")
        assert source_path(cam)["sourceOnDemand"] is False
        # Issiq asosiy yo'l doimiy ro'yxatga ham tushadi, aks holda
        # reconciler uni o'chirib yuborardi.
        assert "k1" in desired_paths([cam])
    finally:
        _warm.clear()


def test_issiq_muddat_qisqartirilmaydi():
    """Sub yo'l 10 daqiqaga issiq bo'lsa, asosiy yo'lning qisqa muddati
    uni qisqartirib yubormasligi kerak."""
    from media.sync import WARM_MAIN_TTL, _warm as W
    try:
        mark_warm("k1")                      # uzun muddat (sub)
        uzun = W["k1"]
        mark_warm("k1", WARM_MAIN_TTL)       # qisqa muddat
        assert W["k1"] == uzun, "muddat qisqartirildi"
    finally:
        _warm.clear()

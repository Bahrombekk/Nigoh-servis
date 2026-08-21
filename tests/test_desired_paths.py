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

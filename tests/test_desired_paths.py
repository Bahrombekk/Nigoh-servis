from media.sync import (
    SUB_SUFFIX,
    TRANSCODE_SUFFIX,
    _warm,
    desired_paths,
    mark_warm,
    source_path,
)


def _cam(**extra):
    base = {"slug": "k1", "ip": "10.0.0.2", "port": 554, "rtsp_path": "/s",
            "username": "", "password": "", "enabled": True,
            "transcode": False, "always_on": False, "sub_path": "",
            "node_id": 1}
    base.update(extra)
    return base


def test_desired_paths_asosiy():
    wanted = desired_paths([_cam()])
    assert "k1" in wanted
    assert f"~^[a-z0-9_]+{TRANSCODE_SUFFIX}$" in wanted   # o'girish shabloni
    assert wanted["k1"]["sourceOnDemand"] is True


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

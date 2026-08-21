"""mediamtx.yml ichidagi ulanish sozlamalari — jimgina yo'qolib qolmasin.

Bu sozlamalarning har biri aniq nosozlikni yopadi, lekin ularsiz ham
MediaMTX muammosiz ko'tariladi — ya'ni tushib qolsa hech kim sezmaydi.
Shuning uchun test.
"""
import yaml

from media import sync


def _conf(**node) -> dict:
    text = sync.build_config([], node=node or None)
    return yaml.safe_load(text)


def test_ice_tcp_zaxirasi_yoqilgan():
    """UDP yopiq tarmoqda brauzer HLS'ga tushmasligi uchun."""
    conf = _conf()
    assert conf["webrtcLocalUDPAddress"] == f":{sync.WEBRTC_UDP_PORT}"
    assert conf["webrtcLocalTCPAddress"] == f":{sync.WEBRTC_TCP_PORT}"


def test_write_queue_standartdan_katta():
    """512 ko'p tomoshabinda to'ladi va MediaMTX paketlarni tashlaydi."""
    conf = _conf()
    assert conf["writeQueueSize"] >= 1024
    # MediaMTX ikkining darajasini talab qiladi.
    assert conf["writeQueueSize"] & (conf["writeQueueSize"] - 1) == 0


def test_uzoq_tugun_ozining_manzilini_beradi():
    """Brauzer uzoq tugunga to'g'ridan ulanadi — manzil o'sha tugunniki."""
    conf = _conf(name="Samarqand", api_base="http://10.0.0.5:9997",
                 public_host="samarqand.example.uz", rtsp_port=8554,
                 hls_port=8888, webrtc_port=8889)
    assert conf["webrtcAdditionalHosts"] == ["samarqand.example.uz"]


def test_hls_oddiy_fmp4_bolib_qoladi():
    """Pleyer lowLatencyMode: false bilan yuradi — server ham LL-HLS emas."""
    conf = _conf()
    assert conf["hlsVariant"] == "fmp4"
    assert conf.get("hlsLowLatency", False) is False

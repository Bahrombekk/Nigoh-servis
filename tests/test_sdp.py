from core.rtsp_probe import (sdp_codec, sdp_fps, sdp_resolution,
                             sdp_video_control)

DESCRIBE = """RTSP/1.0 200 OK\r
Content-Base: rtsp://10.0.0.1:554/Streaming/Channels/101/\r
\r
v=0
m=video 0 RTP/AVP 96
a=rtpmap:96 H265/90000
a=x-dimensions:2560,1440
a=framerate:25
a=control:trackID=1
"""


def test_sdp_codec():
    assert sdp_codec(DESCRIBE) == "H265"
    assert sdp_codec("a=rtpmap:96 H264/90000") == "H264"
    assert sdp_codec("a=rtpmap:97 HEVC/90000") == "H265"   # sinonim
    assert sdp_codec("hech narsa") == ""


def test_sdp_resolution():
    assert sdp_resolution(DESCRIBE) == "2560x1440"
    assert sdp_resolution("a=framesize:96 1920-1080") == "1920x1080"
    assert sdp_resolution("bo'sh") == ""


def test_sdp_fps():
    assert sdp_fps(DESCRIBE) == 25.0
    assert sdp_fps("a=x-framerate: 12.5") == 12.5
    assert sdp_fps("yo'q") == 0.0


def test_sdp_video_control():
    # nisbiy control Content-Base'ga qo'shiladi
    uri = "rtsp://10.0.0.1:554/Streaming/Channels/101"
    assert sdp_video_control(DESCRIBE, uri) == (
        "rtsp://10.0.0.1:554/Streaming/Channels/101/trackID=1")
    # to'liq URL bo'lsa o'zi qaytadi
    full = DESCRIBE.replace("a=control:trackID=1",
                            "a=control:rtsp://10.0.0.1/full/track1")
    assert sdp_video_control(full, uri) == "rtsp://10.0.0.1/full/track1"
    # control yo'q — so'rov manzili
    assert sdp_video_control("m=video 0 RTP/AVP 96", uri) == uri

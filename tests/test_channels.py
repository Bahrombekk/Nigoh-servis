import pytest
from fastapi import HTTPException

from api.admin import parse_channels, spread_point
from api.helpers import channel_path
from core.fast_start import channel_from_path, channel_marked


def test_parse_channels():
    assert parse_channels("1-4") == [1, 2, 3, 4]
    assert parse_channels("1,3,5-7") == [1, 3, 5, 6, 7]
    assert parse_channels("4-2") == [2, 3, 4]          # teskari oraliq
    assert parse_channels(" 8 , 8 ") == [8]            # takror va bo'shliq
    with pytest.raises(HTTPException):
        parse_channels("abc")
    with pytest.raises(HTTPException):
        parse_channels("")
    with pytest.raises(HTTPException):
        parse_channels("1-600")                        # limitdan oshadi


def test_spread_point():
    assert spread_point(41.0, 69.0, 0, 120) == (41.0, 69.0)   # birinchi joyida
    assert spread_point(41.0, 69.0, 3, 0) == (41.0, 69.0)     # tarqatish o'chiq
    lat, lng = spread_point(41.0, 69.0, 5, 120)
    assert (lat, lng) != (41.0, 69.0)
    assert abs(lat - 41.0) < 0.01 and abs(lng - 69.0) < 0.01  # yaqin atrofda
    # bir xil indeks — bir xil natija (deterministik)
    assert spread_point(41.0, 69.0, 5, 120) == (lat, lng)


def test_channel_path_vendorlar():
    assert channel_path("hikvision", 3, "main") == "/Streaming/Channels/301"
    assert channel_path("hikvision", 3, "sub") == "/Streaming/Channels/302"
    assert channel_path("dahua", 2, "sub") == "/cam/realmonitor?channel=2&subtype=1"
    assert channel_path("reolink", 1, "main") == "/h264Preview_01_main"
    assert channel_path("holowits", 2, "sub") == "/LiveMedia/ch2/Media2"
    assert channel_path("nomalum", 1, "main") == "/stream1"


def test_channel_from_path():
    assert channel_from_path("/Streaming/Channels/101") == 1
    assert channel_from_path("/Streaming/Channels/1602") == 16
    assert channel_from_path("/cam/realmonitor?channel=7&subtype=0") == 7
    assert channel_from_path("/h264Preview_03_main") == 3
    assert channel_from_path("/LiveMedia/ch2/Media1") == 2
    assert channel_from_path("/stream1") == 1          # aniqlanmasa 1


def test_channel_marked_koreatilgan_kanalni_ajratadi():
    """`channel_marked` — kanal ATAYLAB ko'rsatilganmi.

    Takror kamerani aniqlash shu farqqa tayanadi: `/stream1` da kanal
    yo'q (None), `/cam/realmonitor?channel=1&subtype=0` da bor (1).
    Ikkisi bir xil bo'lib qolsa `/stream1` va `/stream2` bitta kamera
    deb tanilardi (`api/admin.py:_rtsp_twin`).
    """
    assert channel_marked("/Streaming/Channels/101") == 1
    assert channel_marked("/cam/realmonitor?channel=1&subtype=0") == 1
    assert channel_marked("/LiveMedia/ch2/Media1") == 2
    assert channel_marked("/stream1") is None
    assert channel_marked("/stream2") is None
    assert channel_marked("") is None
    # channel_from_path o'zgarmadi: aniqlanmasa baribir 1 qaytaradi.
    assert channel_from_path("/stream1") == 1

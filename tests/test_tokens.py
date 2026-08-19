import time

from core import security


def test_stream_token_va_sessiya():
    token = security.stream_token("kamera1")
    # to'g'ri chipta o'z yo'liga o'tadi, boshqa yo'lga o'tmaydi
    assert security.stream_access_ok("10.0.0.5", "kamera1", token)
    assert not security.stream_access_ok("10.0.0.9", "boshqa", token)
    # sessiya ochildi — segmentlar chiptasiz yuradi (o'sha ip+yo'l)
    assert security.stream_access_ok("10.0.0.5", "kamera1", "")
    assert not security.stream_access_ok("10.0.0.6", "kamera1", "")


def test_eskirgan_chipta_rad():
    expired = int(time.time()) - 10
    sig = security._stream_sig("kamera2", expired)
    assert not security.stream_access_ok("10.0.0.5", "kamera2",
                                         f"{expired}.{sig}")


def test_soxta_chipta_rad():
    good = int(time.time()) + 600
    assert not security.stream_access_ok("10.0.0.5", "kamera3",
                                         f"{good}.soxta-imzo")


def test_ichki_chipta():
    tok = security.internal_token()
    assert security.internal_token_ok(tok)
    assert not security.internal_token_ok("")
    assert not security.internal_token_ok(tok + "x")
    # deterministik — launcher va backend bir xil qiymat oladi
    assert security.internal_token() == tok

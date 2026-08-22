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


def test_ichki_resurs_chiptasi_qabul_qilinadi():
    """Tomosha o'rtasida video uzilishining sababi.

    Chipta oqim yo'liga imzolanadi ("kamera_1"), lekin MediaMTX ba'zi
    so'rovlarda ichki resurs bilan murojaat qiladi — HLS variant
    playlisti "kamera_1/video1_stream.m3u8". Aynan tekshirilganda bunday
    so'rov rad etilardi: ishlab chiqarishda o'lchandi — tomosha
    boshlangandan ~25-45 soniya keyin 401 boshlanib, keyin doimiy
    bo'lib qolardi.
    """
    token = security.stream_token("kamera_1")
    assert security.stream_access_ok("1.1.1.1", "kamera_1", token)
    assert security.stream_access_ok("1.1.1.2", "kamera_1/video1_stream.m3u8", token)
    assert security.stream_access_ok("1.1.1.3", "kamera_1/seg7.mp4", token)


def test_yondosh_yolga_otmaydi():
    """Prefiks bo'yicha moslik chegara sifatida "/" talab qiladi —
    "kamera_1" chiptasi "kamera_10" ni ochmasligi kerak."""
    token = security.stream_token("kamera_1")
    assert not security.stream_access_ok("2.2.2.1", "kamera_10", token)
    assert not security.stream_access_ok("2.2.2.2", "kamera_2", token)
    assert not security.stream_access_ok("2.2.2.3", "boshqa/kamera_1", token)


def test_sessiya_ichki_resursni_ham_qoplaydi():
    """401 sikli: sessiya to'liq yo'l bo'yicha saqlanardi.

    Brauzer tokenni faqat birinchi so'rovga qo'shadi; segmentlar
    tokensiz keladi va ular uchun (ip, yo'l) sessiyasi bor. Lekin kalit
    TO'LIQ yo'l bo'yicha olinardi, ya'ni "kamera_1" uchun ochilgan
    sessiya "kamera_1/video1_seg9.mp4" ni qoplamasdi. Ishlab
    chiqarishda o'lchandi (negoh.das-uty.uz, auth endpointi):

        yo'l "kamera_1"                    tokensiz -> 200
        yo'l "kamera_1/video1_stream.m3u8" tokensiz -> 401
        yo'l "kamera_1/video1_seg9.mp4"    tokensiz -> 401
    """
    token = security.stream_token("kamera_9")
    assert security.stream_access_ok("3.3.3.1", "kamera_9", token)
    # endi tokensiz ichki resurslar ham o'sha sessiya orqali o'tadi
    assert security.stream_access_ok("3.3.3.1", "kamera_9/video1_stream.m3u8", "")
    assert security.stream_access_ok("3.3.3.1", "kamera_9/video1_seg9.mp4", "")
    # sessiya boshqa IP ga ham, boshqa oqimga ham tarqalmaydi
    assert not security.stream_access_ok("3.3.3.2", "kamera_9/video1_seg9.mp4", "")
    assert not security.stream_access_ok("3.3.3.1", "kamera_90/video1_seg9.mp4", "")


def test_sessiya_ichki_resursdan_ochilsa_ham_ishlaydi():
    """Birinchi ruxsatli so'rov variant playlisti bo'lishi ham mumkin —
    o'shanda ham sessiya oqim yo'li bo'yicha ochilishi kerak."""
    token = security.stream_token("kamera_8")
    assert security.stream_access_ok("4.4.4.1", "kamera_8/video1_stream.m3u8", token)
    assert security.stream_access_ok("4.4.4.1", "kamera_8/video1_seg1.mp4", "")
    assert security.stream_access_ok("4.4.4.1", "kamera_8", "")


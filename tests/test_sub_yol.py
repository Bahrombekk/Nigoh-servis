"""Sub yo'lini taxmin qilish — yo'li yozilmagan kameralar uchun.

Yo'li bo'sh kamera devorda og'ir asosiy oqimda ochiladi (o'lchovda 1,20
o'rniga 7,88 Mbit/s). Kameraning ikkinchi oqimi esa ko'pincha bor —
shunchaki qo'shishda yozilmagan: shu o'rnatmada bo'sh 23 kameradan
sinalgan 8 tasining 4 tasida sub oqim ishlab turgan edi.

Taxmin qat'iy emas — chaqiruvchi har nomzoddan haqiqatda kadr o'qib
ko'radi (`media.sync.kadr_keladimi`) va faqat bergani saqlanadi.
"""
from core.rtsp_probe import sub_yol_nomzodlari as nomzod


def test_dahua():
    assert nomzod("/cam/realmonitor?channel=1&subtype=0") == [
        "/cam/realmonitor?channel=1&subtype=1"]


def test_hikvision():
    assert nomzod("/Streaming/Channels/101") == ["/Streaming/Channels/102"]
    assert nomzod("/Streaming/Channels/401") == ["/Streaming/Channels/402"]


def test_stream_va_main_shakllari():
    assert nomzod("/stream1") == ["/stream2"]
    assert nomzod("/h264/ch1/main/av_stream") == ["/h264/ch1/sub/av_stream"]


def test_allaqachon_sub_bolgan_yol_takrorlanmaydi():
    """Ikkinchi oqim yo'li berilgan bo'lsa — taxmin qiladigan narsa yo'q."""
    assert nomzod("/cam/realmonitor?channel=1&subtype=1") == []
    assert nomzod("/Streaming/Channels/102") == []


def test_tanib_bolmaydigan_yol():
    """Noma'lum shaklda taxmin qilinmaydi — yolg'on nomzod zarar qiladi."""
    assert nomzod("/live/main_stream_hd") == []
    assert nomzod("") == []
    assert nomzod(None) == []


def test_raqam_chalkashmaydi():
    """Regressiya: "stream10" dagi 1 oqim nomeri EMAS."""
    assert nomzod("/stream10") == []


# ---- sub oqim o'girilishi kodekka qarab hal qilinadi ----

from api.helpers import _hevc  # noqa: E402


class _Qator(dict):
    """`sqlite3.Row` kabi indekslanadigan soxta qator."""

    def __getitem__(self, k):
        if k not in self:
            raise KeyError(k)
        return dict.__getitem__(self, k)


def test_hevc_sub_kodek_boyicha():
    assert _hevc(_Qator(sub_codec="H265", codec="H264")) is True
    assert _hevc(_Qator(sub_codec="hevc", codec="")) is True
    assert _hevc(_Qator(sub_codec="H264", codec="H265")) is False


def test_hevc_sub_bilinmasa_asosiyga_qaraydi():
    assert _hevc(_Qator(sub_codec="", codec="H265")) is True
    assert _hevc(_Qator(sub_codec=None, codec="H264")) is False


def test_hevc_malumot_yoqligida_ogirishga_majburlamaydi():
    """Bilinmaganda o'girish BUYURILMAYDI — bekorga GPU sarflamaslik uchun.

    Bunday holatda eski yo'l qoladi: `transcode` bayrog'i hal qiladi.
    """
    assert _hevc(_Qator(sub_codec="", codec="")) is False
    assert _hevc(_Qator()) is False

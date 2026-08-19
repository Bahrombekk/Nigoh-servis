import hashlib
import re

from core.rtsp_probe import _digest_header


def _md5(s: str) -> str:
    return hashlib.md5(s.encode()).hexdigest()


def test_rfc2617_rasmiy_misol():
    """RFC 2617 3.5 misoli — javob hash aynan mos kelishi shart."""
    ch = ('Digest realm="testrealm@host.com", qop="auth,auth-int", '
          'nonce="dcd98b7102dd2f0e8b11d0f600bfb0c093", '
          'opaque="5ccc069c403ebaf9f0171e9517f40e41"')
    h = _digest_header("Mufasa", "Circle Of Life", "GET", "/dir/index.html",
                       ch, nc=1, cnonce="0a4f113b")
    assert 'response="6629fae49393a05397450978507c4ef1"' in h
    assert "qop=auth" in h and "nc=00000001" in h
    assert 'opaque="5ccc069c403ebaf9f0171e9517f40e41"' in h


def test_qop_qoshtirnoqsiz_va_nc():
    ch = 'Digest realm="cam", nonce="abc", qop=auth'
    h = _digest_header("u", "p", "SETUP", "rtsp://1.2.3.4/s", ch, nc=2)
    assert "nc=00000002" in h
    cnonce = re.search(r'cnonce="(\w+)"', h).group(1)
    ha1, ha2 = _md5("u:cam:p"), _md5("SETUP:rtsp://1.2.3.4/s")
    expected = _md5(f"{ha1}:abc:00000002:{cnonce}:auth:{ha2}")
    assert f'response="{expected}"' in h


def test_qopsiz_eski_formula():
    ch = 'Digest realm="cam", nonce="abc"'
    h = _digest_header("u", "p", "DESCRIBE", "rtsp://1.2.3.4/s", ch)
    ha1, ha2 = _md5("u:cam:p"), _md5("DESCRIBE:rtsp://1.2.3.4/s")
    assert f'response="{_md5(f"{ha1}:abc:{ha2}")}"' in h
    assert "qop" not in h and "nc=" not in h and "cnonce" not in h

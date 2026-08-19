"""Nigoh — qurilma pasporti: ishlab chiqaruvchi, model, firmware, seriya.

Ikki manba, ikkalasi ham kameraning web-portida:

  1. ONVIF `GetDeviceInformation` — standart, aksariyat qurilma qo'llaydi;
  2. zaxira: Hikvision ISAPI `/ISAPI/System/deviceInfo`.

Ma'lumot ixtiyoriy bezak emas: firmware'i eski qurilmalarni topish,
ta'minotchi bilan gaplashish va zaxira qism buyurtmasi shu yerdan
boshlanadi.
"""
import re
import urllib.error
import urllib.request

from .fast_start import HTTP_PORT, TIMEOUT, _auth_opener, _security_header, _soap

_DEVICE_NS = "http://www.onvif.org/ver10/device/wsdl"
_ONVIF_PATHS = ("/onvif/device_service", "/onvif/Device")

FIELDS = ("manufacturer", "model", "firmware", "serial", "mac")


def _tag(text: str, name: str) -> str:
    """<...Name>qiymat</...Name> — namespace prefiksidan qat'i nazar."""
    match = re.search(fr"<[^<>]*\b{name}\b[^<>]*>([^<]*)</", text)
    return match.group(1).strip() if match else ""


def parse_onvif(resp: str) -> dict:
    return {
        "manufacturer": _tag(resp, "Manufacturer"),
        "model": _tag(resp, "Model"),
        "firmware": _tag(resp, "FirmwareVersion"),
        "serial": _tag(resp, "SerialNumber"),
        "mac": "",                    # ONVIF bu javobda MAC bermaydi
    }


def parse_isapi(xml: str) -> dict:
    return {
        "manufacturer": _tag(xml, "manufacturer") or "Hikvision",
        "model": _tag(xml, "model"),
        "firmware": _tag(xml, "firmwareVersion"),
        "serial": _tag(xml, "serialNumber"),
        "mac": _tag(xml, "macAddress"),
    }


def _has_info(info: dict) -> bool:
    return any(info.get(f) for f in ("model", "firmware", "serial"))


def onvif_info(ip: str, username: str, password: str) -> dict | None:
    body = f'<tds:GetDeviceInformation xmlns:tds="{_DEVICE_NS}"/>'
    for path in _ONVIF_PATHS:
        url = f"http://{ip}:{HTTP_PORT}{path}"
        try:
            resp = _soap(url, _security_header(username, password), body)
        except (urllib.error.URLError, OSError):
            continue
        info = parse_onvif(resp)
        if _has_info(info):
            return info
    return None


def isapi_info(ip: str, username: str, password: str) -> dict | None:
    url = f"http://{ip}:{HTTP_PORT}/ISAPI/System/deviceInfo"
    try:
        with _auth_opener(url, username, password).open(url,
                                                        timeout=TIMEOUT) as res:
            xml = res.read(65536).decode("utf-8", "replace")
    except (urllib.error.URLError, OSError):
        return None
    info = parse_isapi(xml)
    return info if _has_info(info) else None


def device_info(ip: str, username: str = "",
                password: str = "") -> dict | None:
    """Qurilma pasporti yoki None (javob bermadi / qo'llamaydi)."""
    if not ip:
        return None
    return (onvif_info(ip, username, password)
            or isapi_info(ip, username, password))

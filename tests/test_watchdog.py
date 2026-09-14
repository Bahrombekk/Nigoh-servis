"""Kuzatuvchi: "jarayon tirik, lekin port o'lgan" holatidan chiqish.

Ishlab chiqarishda bo'lgan holat: uvicorn SIGTERM olib "graceful
shutdown" ga o'tgan, tinglash soketini yopgan, lekin ochiq SSE ulanishi
(`/api/v1/events`) yopilmagani uchun shu holatda muddatsiz qotib qolgan.
Jarayon tirik, Docker "ishlayapti" deb bilgan, port esa ulanish qabul
qilmagan — nginx HLS auth'ga 500, MediaMTX RTSP auth'ga 401 bergan.

Bu testlar kuzatuvchining ikki xususiyatini qo'riqlaydi:

  * bitta tasodifiy xatodan jarayonni o'ldirmaydi (fayl deskriptori
    vaqtincha tugashi, navbat to'lishi — bularning hammasi o'tkinchi);
  * lokal ishlab chiqishda umuman yoqilmaydi — u yerda jarayonni
    qaytaradigan Docker yo'q, o'ldirish faqat zarar.
"""
from core import watchdog


def test_konteynerdan_tashqarida_ochiq(monkeypatch):
    monkeypatch.delenv("WATCHDOG", raising=False)
    monkeypatch.setattr(watchdog, "_in_container", lambda: False)
    assert watchdog._enabled() is False


def test_konteynerda_yoqiq(monkeypatch):
    monkeypatch.delenv("WATCHDOG", raising=False)
    monkeypatch.setattr(watchdog, "_in_container", lambda: True)
    assert watchdog._enabled() is True


def test_majburan_ochirish(monkeypatch):
    monkeypatch.setenv("WATCHDOG", "0")
    monkeypatch.setattr(watchdog, "_in_container", lambda: True)
    assert watchdog._enabled() is False


def test_yopiq_port_aniqlanadi():
    # Hech kim tinglamaydigan port — ulanish rad etiladi.
    assert watchdog._alive(1) is False


def test_ochiq_port_aniqlanadi():
    import socket
    server = socket.socket()
    server.bind(("127.0.0.1", 0))
    server.listen(1)
    try:
        assert watchdog._alive(server.getsockname()[1]) is True
    finally:
        server.close()


def test_bitta_xatodan_olmaydi(monkeypatch):
    """Chegara — FAILS_BEFORE_EXIT ta KETMA-KET xato, bittasi emas."""
    javoblar = iter([False, True, False, False])
    olim = []

    monkeypatch.setattr(watchdog, "_alive", lambda port: next(javoblar))
    monkeypatch.setattr(watchdog.os, "_exit", lambda code: olim.append(code))
    monkeypatch.setattr(watchdog, "STARTUP_GRACE", 0)
    monkeypatch.setattr(watchdog, "CHECK_INTERVAL", 0)
    monkeypatch.setattr(watchdog.time, "sleep", lambda s: None)

    try:
        watchdog._loop(9999)
    except StopIteration:
        pass
    # Ikkita xato ketma-ket kelmadi (orasida bitta muvaffaqiyat bor) —
    # jarayon tugatilmasligi kerak.
    assert olim == []

"""Takroriy xato urinishlarni sekinlashtirish (ip bo'yicha).

Nima uchun alohida modul: bir xil mexanizm ikki joyda kerak —
konsolga kirish (parol taxmin qilish) va API kaliti. Ikkalasida ham
qoida bir xil: bir necha bepul urinish, keyin ikki barobarlanadigan
kutish, uzoq tinchlikdan keyin hisob unutiladi.

Hisob XOTIRADA turadi. Jarayon qayta ishga tushsa nolga qaytadi —
bu ataylab: qulf bazaga yozilsa, shu bilan o'zi DoS quroliga
aylanardi (begona ip nomidan urinib, haqiqiy foydalanuvchini
qulflab qo'yish mumkin bo'lardi).
"""
import threading
import time


class Throttle:
    """Ip bo'yicha eksponensial kutish.

    `free` — shu songacha kutish yo'q (odam parolni adashtirishi normal).
    `max_delay` — kutishning yuqori chegarasi.
    `ttl` — shuncha tinch turgan ip hisobi unutiladi.
    """

    def __init__(self, free: int = 5, max_delay: float = 30.0,
                 ttl: float = 3600.0, limit: int = 1000) -> None:
        self.free = free
        self.max_delay = max_delay
        self._ttl = ttl
        self._limit = limit
        self._fails: dict[str, tuple[int, float]] = {}
        self._lock = threading.Lock()

    def retry_after(self, ip: str) -> float:
        """Shu ip yana urinishi uchun necha soniya qolgani (0 — mumkin)."""
        now = time.monotonic()
        with self._lock:
            if len(self._fails) > self._limit:    # xotira cheksiz o'smasin
                for k, (_, t) in list(self._fails.items()):
                    if now - t > self._ttl:
                        self._fails.pop(k, None)
            count, last = self._fails.get(ip, (0, 0.0))
            if now - last > self._ttl or count < self.free:
                return 0.0
            wait = min(2.0 ** (count - self.free), self.max_delay)
            return max(0.0, last + wait - now)

    def note_fail(self, ip: str) -> None:
        now = time.monotonic()
        with self._lock:
            count, last = self._fails.get(ip, (0, 0.0))
            if now - last > self._ttl:
                count = 0
            self._fails[ip] = (count + 1, now)

    def clear(self, ip: str) -> None:
        with self._lock:
            self._fails.pop(ip, None)

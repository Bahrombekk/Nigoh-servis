"""Nigoh — ochilish vaqti o'lchovi.

"Sekin ochilyapti" degan shikoyatga javob berish uchun bitta raqam
yetmaydi: ochilish uch bo'lakdan iborat va ularning har biri butunlay
boshqa narsa haqida gapiradi.

    stream_ms   /cameras/{id}/stream javobi — backend va MediaMTX'da
                yo'lni tayyorlash. Sekin bo'lsa: MediaMTX'da ortiqcha
                yo'llar (pending_paths) yoki uzoq tugun API'si.
    signal_ms   WHEP so'rovi va javob — signalizatsiya. Sekin bo'lsa:
                tarmoq/proksi. HLS'da bu bosqich yo'q (0).
    frame_ms    birinchi kadr — kameradan keyframe kutish. Sekin bo'lsa:
                kameradagi I Frame Interval (registratorda sozlanadi).
    total_ms    foydalanuvchi ko'rgan to'liq vaqt.

Namunalar xotirada, halqa buferda — baza ham, tashqi tizim ham kerak
emas. Server qayta ishga tushsa hisob noldan boshlanadi, bu maqsadga
zid emas: o'lchov "hozir qanday" degan savolga javob beradi.
"""
import threading
from collections import deque

# Har transport uchun oxirgi shuncha namuna. 512 ta p95 uchun yetarli
# va xotirada bir necha o'nlab kilobayt.
MAX_SAMPLES = 512

FIELDS = ("stream_ms", "signal_ms", "frame_ms", "total_ms")

# Yovvoyi qiymat statistikani buzmasin: o'lchov brauzerdan keladi,
# uxlab qolgan yorliq soatlab "ochilgan" deb yozishi mumkin.
MAX_MS = 120_000

_samples: dict[str, deque] = {}
_lock = threading.Lock()


def record(transport: str, sample: dict) -> None:
    """Bitta ochilishni yozadi. Transport: webrtc / hls."""
    row = {}
    for field in FIELDS:
        try:
            value = int(sample.get(field) or 0)
        except (TypeError, ValueError):
            value = 0
        row[field] = min(MAX_MS, max(0, value))
    with _lock:
        queue = _samples.get(transport)
        if queue is None:
            queue = _samples[transport] = deque(maxlen=MAX_SAMPLES)
        queue.append(row)


def percentile(values: list[int], fraction: float) -> int:
    """Eng yaqin tartib statistikasi — interpolatsiyasiz, yetarli."""
    if not values:
        return 0
    ordered = sorted(values)
    index = int(round((len(ordered) - 1) * fraction))
    return ordered[min(max(index, 0), len(ordered) - 1)]


def summary() -> dict:
    """Transport kesimida p50/p95 — /health uchun."""
    with _lock:
        snapshot = {name: list(queue) for name, queue in _samples.items()}
    out: dict[str, dict] = {}
    for transport, rows in snapshot.items():
        if not rows:
            continue
        stats: dict = {"n": len(rows)}
        for field in FIELDS:
            values = [row[field] for row in rows]
            stats[field] = {"p50": percentile(values, 0.5),
                            "p95": percentile(values, 0.95)}
        out[transport] = stats
    return out


def reset() -> None:
    with _lock:
        _samples.clear()

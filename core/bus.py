"""Nigoh — jarayon ichidagi oddiy pub/sub (SSE abonentlari uchun).

Fon thread'lari (health sweep, reconciler) holat o'zgarishlarini shu
yerga e'lon qiladi; `/events` SSE endpointi obuna bo'lib brauzerga yoki
asosiy tizimga uzatadi. Redis va shunga o'xshashlar kerak emas — hammasi
bitta jarayonda.

Thread'lar bilan asyncio orasidagi ko'prik: publisher'lar istalgan
thread'dan `publish()` chaqiradi, hodisa event loop'ga
`call_soon_threadsafe` orqali o'tadi.
"""
import asyncio
import threading

MAX_SUBSCRIBERS = 50    # SSE ulanish chegarasi — undan ortiq 503 oladi
QUEUE_SIZE = 200        # abonent shu qadar orqada qolsa hodisa tashlanadi

_subscribers: list[asyncio.Queue] = []
_lock = threading.Lock()
_loop: asyncio.AbstractEventLoop | None = None


def set_loop(loop: asyncio.AbstractEventLoop) -> None:
    """Ilova ishga tushganda chaqiriladi — publisher thread'lar hodisani
    shu loop orqali yetkazadi."""
    global _loop
    _loop = loop


def subscribe() -> asyncio.Queue | None:
    """Yangi abonent navbati. Chegara to'lgan bo'lsa None."""
    q: asyncio.Queue = asyncio.Queue(maxsize=QUEUE_SIZE)
    with _lock:
        if len(_subscribers) >= MAX_SUBSCRIBERS:
            return None
        _subscribers.append(q)
    return q


def unsubscribe(q: asyncio.Queue) -> None:
    with _lock:
        try:
            _subscribers.remove(q)
        except ValueError:
            pass


def subscriber_count() -> int:
    with _lock:
        return len(_subscribers)


def publish(event: str, data: dict) -> None:
    """Hodisa e'lon qilish — istalgan thread'dan xavfsiz.

    Abonent bo'lmasa yoki loop hali o'rnatilmagan bo'lsa — jim o'tadi:
    e'lon qilish hech qachon publisher'ni to'xtatmaydi.
    """
    loop = _loop
    if loop is None or loop.is_closed():
        return
    with _lock:
        targets = list(_subscribers)
    for q in targets:
        loop.call_soon_threadsafe(_offer, q, (event, data))


def _offer(q: asyncio.Queue, item: tuple) -> None:
    try:
        q.put_nowait(item)
    except asyncio.QueueFull:
        pass    # sekin abonent — hodisa yo'qoladi, oqim to'xtamaydi

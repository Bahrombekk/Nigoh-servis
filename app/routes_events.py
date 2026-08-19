"""Nigoh — SSE: kamera holati o'zgarishlarini jonli uzatish.

Asosiy tizim 5000 kamerani poll qilmasin — bitta `GET /events` ulanishi
holat o'zgarishlarini o'zi yetkazadi:

    event: state
    data: {"id": 45, "external_id": "...", "state": "offline", "at": "..."}

Holatlar: `online / offline` (health sweep, ~60 s ichida) va
`stalled / online` (reconciler bayt hisobi, ~30 s ichida). Boshlang'ich
holatni ulanishdan oldin `GET /cameras/status` dan oling.
"""
import asyncio
import json

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse

from core import bus

# Prefiks nisbiy — create_app uni /api/v1 (asosiy) va /api (eski) ostida ulaydi.
router = APIRouter(tags=["events"])

KEEPALIVE_S = 15.0    # proksi (nginx) jim ulanishni uzmasin


@router.get("/events")
async def sse_events():
    """Server-Sent Events oqimi — `curl -N .../api/v1/events` bilan sinang."""
    q = bus.subscribe()
    if q is None:
        raise HTTPException(
            503, f"SSE abonentlari chegarasi to'lgan ({bus.MAX_SUBSCRIBERS})")

    async def gen():
        try:
            yield ": ulandi\n\n"
            while True:
                try:
                    event, data = await asyncio.wait_for(q.get(),
                                                         timeout=KEEPALIVE_S)
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
                    continue
                yield (f"event: {event}\n"
                       f"data: {json.dumps(data, ensure_ascii=False)}\n\n")
        finally:
            # Mijoz uzilganda ham (generator bekor qilinadi) joy bo'shaydi.
            bus.unsubscribe(q)

    return StreamingResponse(gen(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        # nginx SSE'ni buferlamasin — hodisalar darhol yetib borsin.
        "X-Accel-Buffering": "no",
    })

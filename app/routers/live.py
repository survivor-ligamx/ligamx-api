"""Marcadores EN VIVO por streaming (Server-Sent Events).

En vez de que el cliente haga polling cada pocos segundos, se mantiene una
conexion abierta y el servidor empuja los cambios. La consulta a la fuente
(ESPN) esta cacheada, asi que muchos clientes comparten una sola llamada de red.

Uso desde el navegador:
    const es = new EventSource("/live/stream");
    es.addEventListener("live", e => console.log(JSON.parse(e.data)));
"""
import asyncio
import json
import time

from fastapi import APIRouter, Request, Query
from fastapi.responses import StreamingResponse

from app.scrapers.espn_requests_scraper import ESPNRequestsScraper
from app.cache import cached

router = APIRouter()


@cached(15)
def _live_snapshot():
    """Partidos en vivo (cacheado 15s para compartir entre conexiones)."""
    return ESPNRequestsScraper().get_live_matches()


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, default=str, ensure_ascii=False)}\n\n"


@router.get("/live/stream")
async def live_stream(
    request: Request,
    interval: int = Query(15, ge=1, le=60, description="Segundos entre actualizaciones"),
    max_seconds: int = Query(900, ge=1, le=7200, description="Duracion maxima de la conexion"),
):
    """Stream SSE de marcadores en vivo de Liga MX. Empuja un evento `live` con
    los partidos en curso (solo cuando cambian; entre medias manda keepalives) y
    cierra con un evento `end` al llegar a `max_seconds` (el cliente reconecta)."""

    async def event_gen():
        start = time.monotonic()

        async def snapshot():
            try:
                matches = await asyncio.to_thread(_live_snapshot)
                return {"matches": matches, "count": len(matches)}
            except Exception as e:
                return {"error": str(e)}

        # Primer evento inmediato (no dependemos de is_disconnected para el 1er push)
        body = await snapshot()
        yield _sse("live", body)
        last_payload = json.dumps(body, default=str)

        while True:
            if time.monotonic() - start >= max_seconds:
                yield _sse("end", {"reason": "max_seconds"})
                break
            await asyncio.sleep(interval)
            if await request.is_disconnected():
                break
            body = await snapshot()
            payload = json.dumps(body, default=str)
            if payload != last_payload:
                yield _sse("live", body)
                last_payload = payload
            else:
                yield ": keepalive\n\n"

    return StreamingResponse(
        event_gen(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",  # evita buffering en proxies (nginx/Render)
        },
    )

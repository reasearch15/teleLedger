import asyncio
from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse

from app.api.dependencies import get_stream_current_user
from app.models.user import User
from app.websocket.events import event_broker

router = APIRouter(prefix="/api", tags=["events"])


@router.get("/events")
async def live_events(
    _: Annotated[User, Depends(get_stream_current_user)],
) -> StreamingResponse:
    """Stream live invalidation events to authenticated dashboards."""

    async def stream() -> AsyncIterator[str]:
        async with event_broker.subscribe() as queue:
            yield "retry: 2000\n\n"
            while True:
                try:
                    payload = await asyncio.wait_for(queue.get(), timeout=15)
                    yield f"data: {payload}\n\n"
                except TimeoutError:
                    yield ": keepalive\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Any


from app.core.logging import get_logger

logger = get_logger(__name__)


class LiveEventType(StrEnum):
    """Canonical live-update event names consumed by dashboards."""

    PAYMENT_CREATED = "payment_created"
    PAYMENT_CLAIMED = "payment_claimed"
    PAYMENT_UNCLAIMED = "payment_unclaimed"
    PAYMENT_DONE = "payment_done"
    PAYMENT_REOPENED = "payment_reopened"
    PAYMENT_DISMISSED = "payment_dismissed"
    PAYMENT_ALL_COADMINS_DECLINED = "payment_all_coadmins_declined"
    PAYMENT_DECLINED_REVIEW_DISMISSED = "payment_declined_review_dismissed"
    PAYMENT_DELETED = "payment_deleted"
    CASHOUT_CREATED = "cashout_created"
    CASHOUT_SENT = "cashout_sent"
    CASHOUT_COMPLETED = "cashout_completed"
    CASHOUT_CANCELLED = "cashout_cancelled"
    CASHOUT_NOTES_UPDATED = "cashout_notes_updated"
    INQUIRY_MESSAGE_CREATED = "inquiry_message_created"
    INQUIRY_MESSAGE_UPDATED = "inquiry_message_updated"
    INQUIRY_MEDIA_READY = "inquiry_media_ready"
    SETTLEMENT_CREATED = "settlement_created"
    SETTLEMENT_DONE = "settlement_done"
    LEDGER_CHANGED = "ledger_changed"
    STAFF_CHANGED = "staff_changed"


class EventBroker:
    """Process-local fan-out for lightweight dashboard invalidation events."""

    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[str]] = set()

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[str]]:
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=32)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    async def publish(
        self,
        event: LiveEventType | str,
        *,
        broadcast: bool = False,
        **data: Any,
    ) -> None:
        payload = json.dumps({"event": str(event), **data})
        self.ingest(payload)
        if broadcast:
            from app.websocket.cross_process import notify_live_event

            await notify_live_event(payload)
            logger.info(
                "live_event_broadcast",
                extra={"sse_event": str(event)},
            )

    def ingest(self, payload: str) -> None:
        """Fan out one serialized payload to in-process SSE subscribers."""
        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(payload)


event_broker = EventBroker()

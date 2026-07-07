from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from enum import StrEnum
from typing import Any


class LiveEventType(StrEnum):
    """Canonical live-update event names consumed by dashboards."""

    PAYMENT_CREATED = "payment_created"
    PAYMENT_CLAIMED = "payment_claimed"
    PAYMENT_UNCLAIMED = "payment_unclaimed"
    PAYMENT_DONE = "payment_done"
    PAYMENT_REOPENED = "payment_reopened"
    CASHOUT_CREATED = "cashout_created"
    CASHOUT_SENT = "cashout_sent"
    CASHOUT_COMPLETED = "cashout_completed"
    CASHOUT_CANCELLED = "cashout_cancelled"
    CASHOUT_NOTES_UPDATED = "cashout_notes_updated"
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

    async def publish(self, event: LiveEventType | str, **data: Any) -> None:
        payload = json.dumps({"event": str(event), **data})
        for queue in tuple(self._subscribers):
            if queue.full():
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
            queue.put_nowait(payload)


event_broker = EventBroker()

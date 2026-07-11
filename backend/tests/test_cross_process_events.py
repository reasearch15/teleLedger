from __future__ import annotations

import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.websocket import cross_process
from app.websocket.cross_process import LIVE_EVENTS_CHANNEL, notify_live_event
from app.websocket.events import EventBroker, LiveEventType


@pytest.mark.asyncio
async def test_notify_live_event_uses_postgres_channel(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    execute = AsyncMock()
    commit = AsyncMock()
    connection = SimpleNamespace(execute=execute, commit=commit)

    @asynccontextmanager
    async def fake_connect():
        yield connection

    monkeypatch.setattr(
        cross_process,
        "engine",
        SimpleNamespace(connect=fake_connect),
    )

    payload = json.dumps({"event": "cashout_completed", "cashout_id": 9})
    await notify_live_event(payload)

    execute.assert_awaited_once()
    _, parameters = execute.await_args.args
    assert parameters["channel"] == LIVE_EVENTS_CHANNEL
    assert parameters["payload"] == payload
    commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_event_broker_broadcast_does_not_require_local_subscribers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    notify = AsyncMock()
    monkeypatch.setattr("app.websocket.cross_process.notify_live_event", notify)
    broker = EventBroker()

    await broker.publish(
        LiveEventType.CASHOUT_COMPLETED,
        cashout_id=4,
        broadcast=True,
    )

    notify.assert_awaited_once_with(
        json.dumps({"event": "cashout_completed", "cashout_id": 4})
    )

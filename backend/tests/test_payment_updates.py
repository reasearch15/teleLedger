import json

import pytest
from fastapi.routing import APIRoute

from app.api.dependencies import get_stream_current_user
from app.api.routes.events import live_events, router
from app.db.session import get_session
from app.websocket.events import EventBroker, LiveEventType


@pytest.mark.asyncio
async def test_event_broker_fans_out_to_all_dashboards() -> None:
    broker = EventBroker()

    async with broker.subscribe() as first, broker.subscribe() as second:
        await broker.publish(LiveEventType.PAYMENT_CLAIMED, payment_id=42)

        expected = json.dumps({"event": "payment_claimed", "payment_id": 42})
        assert await first.get() == expected
        assert await second.get() == expected


def test_live_events_closes_database_session_before_streaming() -> None:
    route = next(
        route
        for route in router.routes
        if isinstance(route, APIRoute) and route.endpoint is live_events
    )
    auth_dependency = next(
        dependency
        for dependency in route.dependant.dependencies
        if dependency.call is get_stream_current_user
    )
    session_dependency = next(
        dependency
        for dependency in auth_dependency.dependencies
        if dependency.call is get_session
    )

    assert session_dependency.scope == "function"

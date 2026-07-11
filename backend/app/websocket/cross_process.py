from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable

import asyncpg
from sqlalchemy import text

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import engine

logger = get_logger(__name__)
LIVE_EVENTS_CHANNEL = "teleledger_live_events"


def postgres_dsn() -> str:
    """Build a plain PostgreSQL DSN for asyncpg LISTEN connections."""
    settings = get_settings()
    password = settings.database_password.get_secret_value()
    return (
        f"postgresql://{settings.database_user}:{password}"
        f"@{settings.database_host}:{settings.database_port}/{settings.database_name}"
    )


async def notify_live_event(payload: str) -> None:
    """Publish one live-event payload to every connected API process."""
    payload_bytes = len(payload.encode("utf-8"))
    if payload_bytes >= 8000:
        logger.error(
            "live_event_payload_too_large",
            extra={"payload_bytes": payload_bytes},
        )
        return
    try:
        async with engine.connect() as connection:
            await connection.execute(
                text("SELECT pg_notify(:channel, :payload)"),
                {"channel": LIVE_EVENTS_CHANNEL, "payload": payload},
            )
            await connection.commit()
        logger.info(
            "live_event_notify_sent",
            extra={"payload_bytes": payload_bytes},
        )
    except Exception:
        logger.exception("live_event_notify_failed")


async def run_live_event_listener(
    on_payload: Callable[[str], Awaitable[None]],
) -> None:
    """Listen for NOTIFY payloads and forward them into the local event broker."""
    connection: asyncpg.Connection | None = None
    loop = asyncio.get_running_loop()

    def _callback(
        _connection: asyncpg.Connection,
        _pid: int,
        channel: str,
        payload: str,
    ) -> None:
        if channel != LIVE_EVENTS_CHANNEL:
            return
        loop.call_soon_threadsafe(lambda: asyncio.create_task(on_payload(payload)))

    try:
        connection = await asyncpg.connect(postgres_dsn())
        await connection.add_listener(LIVE_EVENTS_CHANNEL, _callback)
        logger.info("live_event_listener_started")
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.exception("live_event_listener_failed")
        raise
    finally:
        if connection is not None:
            try:
                await connection.remove_listener(LIVE_EVENTS_CHANNEL, _callback)
            except Exception:
                logger.exception("live_event_listener_remove_failed")
            await connection.close()
        logger.info("live_event_listener_stopped")

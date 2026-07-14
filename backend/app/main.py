from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.router import api_router
from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.db.session import close_database, warm_database_pool
from app.websocket.cross_process import run_live_event_listener
from app.websocket.events import event_broker, event_name_from_payload

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger(__name__)


async def _forward_cross_process_live_event(payload: str) -> None:
    event_broker.ingest(payload)
    logger.info(
        "live_event_received_from_listener",
        extra={"sse_event": event_name_from_payload(payload)},
    )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    """Manage application-level resources."""
    logger.info("application_started", extra={"environment": settings.environment})
    live_event_listener_task = asyncio.create_task(
        run_live_event_listener(_forward_cross_process_live_event),
        name="live-event-listener",
    )
    if settings.environment == "development":
        await warm_database_pool()
        logger.info("database_pool_warmed")
    try:
        yield
    finally:
        live_event_listener_task.cancel()
        await asyncio.gather(live_event_listener_task, return_exceptions=True)
        await close_database()
        logger.info("application_stopped")


def create_application() -> FastAPI:
    """Build and configure the FastAPI application."""
    application = FastAPI(
        title=settings.app_name,
        debug=settings.debug,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_strings,
        allow_credentials=True,
        allow_methods=["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"],
        allow_headers=["*"],
    )
    application.include_router(api_router)
    return application


app = create_application()

from pathlib import Path

from fastapi import APIRouter, status
from sqlalchemy import text

import app.telegram.listener_health as listener_health
from app.core.config import get_settings
from app.db.session import engine
from app.schemas.health import (
    HealthResponse,
    ListenerHealthResponse,
    ReadinessCheck,
    ReadinessResponse,
)

router = APIRouter(tags=["health"])


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Check API health",
)
async def health_check() -> HealthResponse:
    """Return the process health status without querying dependencies."""
    return HealthResponse(status="ok")


@router.get(
    "/ready",
    response_model=ReadinessResponse,
    status_code=status.HTTP_200_OK,
    summary="Check API dependency readiness",
)
async def readiness_check() -> ReadinessResponse:
    """Return cheap readiness checks without external Telegram calls or secrets."""
    settings = get_settings()
    checks: dict[str, ReadinessCheck] = {}
    checks["database"] = await _database_ready()
    checks["telegram_workflow_config"] = _telegram_workflow_config_ready()
    checks["telegram_listener_config"] = _telegram_listener_config_ready()
    checks["media_storage"] = _media_storage_ready(settings.inquiry_media_dir)
    if any(check.status == "degraded" for check in checks.values()):
        overall = "degraded"
    elif any(check.status == "not_configured" for check in checks.values()):
        overall = "not_configured"
    else:
        overall = "ok"
    return ReadinessResponse(status=overall, checks=checks)


@router.get(
    "/health/listener",
    response_model=ListenerHealthResponse,
    status_code=status.HTTP_200_OK,
    summary="Telegram listener health",
)
async def listener_health_check() -> ListenerHealthResponse:
    """Return the in-process Telegram listener health snapshot.

    Note: this reflects the listener process only when the API and listener
    share memory. When they run separately, the listener process owns the
    authoritative counters; the API still exposes this shape for local/dev
    tooling and future shared-status backends.
    """
    snapshot = listener_health.get_listener_health()
    return ListenerHealthResponse(
        connected=snapshot.connected,
        last_update_at=(
            snapshot.last_update_at.isoformat() if snapshot.last_update_at else None
        ),
        last_reaction_update_at=(
            snapshot.last_reaction_update_at.isoformat()
            if snapshot.last_reaction_update_at
            else None
        ),
        last_reconciliation_at=(
            snapshot.last_reconciliation_at.isoformat()
            if snapshot.last_reconciliation_at
            else None
        ),
        reconciliation_error=snapshot.reconciliation_error,
        listener_restart_count=snapshot.listener_restart_count,
        cashout_group_chat_id=snapshot.cashout_group_chat_id,
    )


async def _database_ready() -> ReadinessCheck:
    try:
        async with engine.connect() as connection:
            await connection.execute(text("SELECT 1"))
    except Exception:
        return ReadinessCheck(status="degraded", detail="unreachable")
    return ReadinessCheck(status="ok")


def _telegram_workflow_config_ready() -> ReadinessCheck:
    settings = get_settings()
    missing = []
    if settings.telegram_cashout_group_id is None:
        missing.append("cashout_group_id")
    if settings.telegram_bot_token is None:
        missing.append("bot_token")
    if missing:
        return ReadinessCheck(
            status="not_configured",
            detail=",".join(missing),
        )
    return ReadinessCheck(status="ok")


def _telegram_listener_config_ready() -> ReadinessCheck:
    settings = get_settings()
    if not settings.telegram_enabled:
        return ReadinessCheck(status="not_configured", detail="listener_disabled")
    required = (
        settings.telegram_api_id,
        settings.telegram_api_hash,
        settings.telegram_session_name,
        settings.telegram_group_target,
    )
    if any(value is None for value in required):
        return ReadinessCheck(status="not_configured", detail="listener_config_incomplete")
    return ReadinessCheck(status="ok")


def _media_storage_ready(root: str) -> ReadinessCheck:
    path = Path(root)
    try:
        path.mkdir(parents=True, exist_ok=True)
    except OSError:
        return ReadinessCheck(status="degraded", detail="media_root_unavailable")
    if not path.is_dir():
        return ReadinessCheck(status="degraded", detail="media_root_not_directory")
    return ReadinessCheck(status="ok")

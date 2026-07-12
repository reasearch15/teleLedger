from fastapi import APIRouter, status

from app.schemas.health import HealthResponse, ListenerHealthResponse
from app.telegram import listener_health

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

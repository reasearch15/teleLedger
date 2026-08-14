from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Health endpoint response."""

    status: Literal["ok"]


class ReadinessCheck(BaseModel):
    """One non-secret readiness signal."""

    status: Literal["ok", "degraded", "not_configured"]
    detail: str | None = None


class ReadinessResponse(BaseModel):
    """Dependency/configuration readiness response."""

    status: Literal["ok", "degraded", "not_configured"]
    checks: dict[str, ReadinessCheck]


class ListenerHealthResponse(BaseModel):
    """Telegram listener health snapshot for operators."""

    connected: bool
    last_update_at: str | None
    last_reaction_update_at: str | None
    last_reconciliation_at: str | None
    reconciliation_error: str | None
    listener_restart_count: int
    cashout_group_chat_id: int | None

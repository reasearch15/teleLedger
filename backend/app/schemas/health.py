from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    """Health endpoint response."""

    status: Literal["ok"]


class ListenerHealthResponse(BaseModel):
    """Telegram listener health snapshot for operators."""

    connected: bool
    last_update_at: str | None
    last_reaction_update_at: str | None
    last_reconciliation_at: str | None
    reconciliation_error: str | None
    listener_restart_count: int
    cashout_group_chat_id: int | None

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Lock


@dataclass
class ListenerHealth:
    """In-process health snapshot for the Telegram listener."""

    connected: bool = False
    last_update_at: datetime | None = None
    last_reaction_update_at: datetime | None = None
    last_reconciliation_at: datetime | None = None
    reconciliation_error: str | None = None
    listener_restart_count: int = 0
    cashout_group_chat_id: int | None = None
    extra: dict[str, object] = field(default_factory=dict)


_lock = Lock()
_health = ListenerHealth()


def get_listener_health() -> ListenerHealth:
    """Return a shallow copy of the current listener health snapshot."""
    with _lock:
        return ListenerHealth(
            connected=_health.connected,
            last_update_at=_health.last_update_at,
            last_reaction_update_at=_health.last_reaction_update_at,
            last_reconciliation_at=_health.last_reconciliation_at,
            reconciliation_error=_health.reconciliation_error,
            listener_restart_count=_health.listener_restart_count,
            cashout_group_chat_id=_health.cashout_group_chat_id,
            extra=dict(_health.extra),
        )


def mark_connected(*, cashout_group_chat_id: int | None = None) -> None:
    with _lock:
        _health.connected = True
        _health.cashout_group_chat_id = cashout_group_chat_id
        _health.last_update_at = datetime.now(UTC)


def mark_disconnected() -> None:
    with _lock:
        _health.connected = False


def mark_restart() -> None:
    with _lock:
        _health.listener_restart_count += 1
        _health.connected = False


def mark_update_received() -> None:
    with _lock:
        _health.last_update_at = datetime.now(UTC)


def mark_reaction_update_received() -> None:
    now = datetime.now(UTC)
    with _lock:
        _health.last_update_at = now
        _health.last_reaction_update_at = now


def mark_reconciliation(
    *,
    error: str | None = None,
) -> None:
    with _lock:
        _health.last_reconciliation_at = datetime.now(UTC)
        _health.reconciliation_error = error

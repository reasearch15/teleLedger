from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from app.core.logging import get_logger
from app.db.repositories.cashout import CashoutRepository
from app.db.session import SessionFactory
from app.models.cashout import (
    CashoutAuditAction,
    CashoutRequestAudit,
    CashoutStatus,
)
from app.websocket.events import LiveEventType, event_broker

logger = get_logger(__name__)


class TelegramMessageFetcher(Protocol):
    """Small Telethon client surface used by the recent reaction fallback."""

    async def get_messages(self, entity: object, *, ids: int) -> object: ...


@dataclass(frozen=True, slots=True)
class CashoutReactionCompletionResult:
    """Outcome of applying a reaction update to one cashout message."""

    completed: bool
    cashout_id: int | None
    reason: str
    matched_cashout: bool = False
    previous_status: str | None = None


async def complete_recent_cashout_reactions(
    client: TelegramMessageFetcher,
    group: object,
    *,
    limit: int = 100,
) -> list[CashoutReactionCompletionResult]:
    """Use fieldless UpdateRecentReactions as a trigger to inspect sent messages."""
    async with SessionFactory() as session:
        repository = CashoutRepository(session)
        message_ids = await repository.list_reaction_candidate_message_ids(limit=limit)

    logger.info(
        "cashout_recent_reaction_scan_started",
        extra={"total": len(message_ids), "limit": limit},
    )
    results: list[CashoutReactionCompletionResult] = []
    for message_id in message_ids:
        try:
            message = await client.get_messages(group, ids=message_id)
        except Exception:
            logger.exception(
                "cashout_recent_reaction_message_fetch_failed",
                extra={"telegram_message_id": message_id},
            )
            continue

        if not _message_has_active_reaction(message):
            logger.info(
                "cashout_recent_reaction_scan_ignored",
                extra={
                    "telegram_message_id": message_id,
                    "completed": False,
                    "reason_ignored": "no_active_reaction",
                    "reaction_summary": _message_reaction_summary(message),
                },
            )
            continue

        logger.info(
            "cashout_recent_reaction_scan_matched",
            extra={
                "telegram_message_id": message_id,
                "reaction_summary": _message_reaction_summary(message),
            },
        )
        results.append(await complete_cashout_from_reaction(message_id))

    logger.info(
        "cashout_recent_reaction_scan_finished",
        extra={
            "total": len(message_ids),
            "completed": sum(1 for item in results if item.completed),
        },
    )
    return results


async def complete_cashout_from_reaction(
    telegram_message_id: int,
) -> CashoutReactionCompletionResult:
    """Complete a sent cashout when any reaction exists on its message."""
    async with SessionFactory() as session, session.begin():
        repository = CashoutRepository(session)
        cashout = await repository.get_by_telegram_message_id_for_update(telegram_message_id)
        if cashout is None:
            logger.info(
                "cashout_reaction_no_matching_cashout",
                extra={
                    "telegram_message_id": telegram_message_id,
                    "matched_cashout": False,
                    "completed": False,
                    "reason_ignored": "no_matching_cashout",
                },
            )
            return CashoutReactionCompletionResult(
                completed=False,
                cashout_id=None,
                reason="no_matching_cashout",
                matched_cashout=False,
            )
        previous_status = cashout.status.value
        if cashout.status == CashoutStatus.COMPLETED:
            logger.info(
                "cashout_reaction_ignored",
                extra={
                    "telegram_message_id": telegram_message_id,
                    "cashout_request_id": cashout.id,
                    "matched_cashout": True,
                    "previous_status": previous_status,
                    "completed": False,
                    "reason_ignored": "already_completed",
                },
            )
            return CashoutReactionCompletionResult(
                completed=False,
                cashout_id=cashout.id,
                reason="already_completed",
                matched_cashout=True,
                previous_status=previous_status,
            )
        if cashout.status == CashoutStatus.CANCELLED:
            logger.info(
                "cashout_reaction_ignored",
                extra={
                    "telegram_message_id": telegram_message_id,
                    "cashout_request_id": cashout.id,
                    "matched_cashout": True,
                    "previous_status": previous_status,
                    "completed": False,
                    "reason_ignored": "cancelled",
                },
            )
            return CashoutReactionCompletionResult(
                completed=False,
                cashout_id=cashout.id,
                reason="cancelled",
                matched_cashout=True,
                previous_status=previous_status,
            )

        previous = {
            "status": previous_status,
            "completed_at": (
                cashout.completed_at.isoformat() if cashout.completed_at is not None else None
            ),
        }
        now = datetime.now(UTC)
        cashout.status = CashoutStatus.COMPLETED
        cashout.completed_at = now
        cashout.telegram_next_attempt_at = None
        await repository.add_audit(
            CashoutRequestAudit(
                cashout_request_id=cashout.id,
                action=CashoutAuditAction.TELEGRAM_REACTION_COMPLETED,
                actor_user_id=None,
                previous_value=previous,
                new_value={
                    "status": cashout.status.value,
                    "completed_at": now.isoformat(),
                    "telegram_message_id": telegram_message_id,
                },
            )
        )
        cashout_id = cashout.id

    await event_broker.publish(
        LiveEventType.CASHOUT_COMPLETED,
        cashout_id=cashout_id,
    )
    await event_broker.publish(LiveEventType.LEDGER_CHANGED)
    logger.info(
        "cashout_reaction_sse_published",
        extra={
            "cashout_request_id": cashout_id,
            "telegram_message_id": telegram_message_id,
            "sse_event": "cashout_completed",
        },
    )
    logger.info(
        "cashout_reaction_completed",
        extra={
            "cashout_request_id": cashout_id,
            "telegram_message_id": telegram_message_id,
            "matched_cashout": True,
            "previous_status": previous_status,
            "completed": True,
        },
    )
    return CashoutReactionCompletionResult(
        completed=True,
        cashout_id=cashout_id,
        reason="completed",
        matched_cashout=True,
        previous_status=previous_status,
    )


def _message_has_active_reaction(message: object | None) -> bool:
    if message is None:
        return False
    reactions = getattr(message, "reactions", None)
    if _has_active_reaction(reactions):
        return True
    if reactions is not None and _has_active_reaction_collection(
        getattr(reactions, "recent_reactions", None)
    ):
        return True
    if isinstance(reactions, Iterable) and not isinstance(reactions, (str, bytes)):
        return _has_active_reaction_collection(reactions)
    return reactions is not None and not hasattr(reactions, "results")


def _has_active_reaction(reactions: object | None) -> bool:
    if reactions is None:
        return False
    results = getattr(reactions, "results", None)
    if results is None:
        return False
    for result in results:
        count = getattr(result, "count", None)
        if not isinstance(count, int) or count > 0:
            return True
    return False


def _has_active_reaction_collection(reactions: object | None) -> bool:
    if reactions is None:
        return False
    if isinstance(reactions, (str, bytes)):
        return bool(reactions)
    if isinstance(reactions, Iterable):
        return any(_reaction_item_active(reaction) for reaction in reactions)
    return True


def _reaction_item_active(reaction: object) -> bool:
    count = getattr(reaction, "count", None)
    if isinstance(count, int):
        return count > 0
    return True


def _message_reaction_summary(message: object | None) -> str | None:
    if message is None:
        return None
    reactions = getattr(message, "reactions", None)
    if reactions is None:
        return None
    results = getattr(reactions, "results", None)
    if results is not None:
        return f"results:{_summarize_reaction_iterable(results)}"
    recent = getattr(reactions, "recent_reactions", None)
    if recent is not None:
        return f"recent:{_summarize_reaction_iterable(recent)}"
    if isinstance(reactions, Iterable) and not isinstance(reactions, (str, bytes)):
        return _summarize_reaction_iterable(reactions)
    return type(reactions).__name__


def _summarize_reaction_iterable(values: Iterable[object]) -> str:
    chunks: list[str] = []
    for index, item in enumerate(values):
        if index >= 8:
            chunks.append("...")
            break
        reaction = getattr(item, "reaction", item)
        emoticon = getattr(reaction, "emoticon", None)
        document_id = getattr(reaction, "document_id", None)
        count = getattr(item, "count", None)
        label = str(emoticon or document_id or type(reaction).__name__)
        chunks.append(f"{label}:{count}" if count is not None else label)
    return "[" + ", ".join(chunks) + "]"

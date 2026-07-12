from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import update

from app.core.logging import get_logger
from app.db.repositories.cashout import CashoutRepository
from app.db.session import SessionFactory
from app.models.cashout import (
    CashoutAuditAction,
    CashoutRequest,
    CashoutRequestAudit,
    CashoutStatus,
)
from app.telegram.peer_ids import chat_ids_equivalent, normalize_telegram_chat_id
from app.telegram.reaction_matching import (
    extract_reaction_emoticons,
    reaction_matches_completion,
)
from app.websocket.events import LiveEventType, event_broker

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class CashoutReactionCompletionResult:
    """Outcome of applying a reaction update to one cashout message."""

    completed: bool
    cashout_id: int | None
    reason: str
    matched_cashout: bool = False
    previous_status: str | None = None
    reaction_emoji: str | None = None


async def complete_recent_cashout_reactions(
    client: object,
    group: object,
    *,
    expected_chat_id: int,
    allowed_reactions: frozenset[str] | None = None,
    limit: int = 100,
) -> list[CashoutReactionCompletionResult]:
    """Use fieldless UpdateRecentReactions as a trigger to inspect sent messages."""
    from app.telegram.cashout_reconciliation import reconcile_pending_cashout_reactions

    return await reconcile_pending_cashout_reactions(
        client,
        group,
        expected_chat_id=expected_chat_id,
        allowed_reactions=allowed_reactions,
        limit=limit,
    )


async def complete_cashout_from_reaction(
    telegram_message_id: int,
    telegram_chat_id: int,
    expected_chat_id: int,
    *,
    allowed_reactions: frozenset[str] | None = None,
    reaction_emoji: str | None = None,
    reactor_user_id: int | None = None,
    source: str = "reaction_event",
) -> CashoutReactionCompletionResult:
    """Complete a sent cashout when a valid completion reaction exists.

    Completion is idempotent: only one ``pending``/``sent``/``failed_to_send`` →
    ``completed`` transition succeeds and emits live events.
    Removing a reaction never reverses completion.
    """
    normalized_chat = normalize_telegram_chat_id(telegram_chat_id)
    normalized_expected = normalize_telegram_chat_id(expected_chat_id)
    if normalized_chat is None or normalized_expected is None:
        logger.info(
            "reaction_mapping_failed",
            extra={
                "telegram_message_id": telegram_message_id,
                "telegram_chat_id": telegram_chat_id,
                "expected_telegram_chat_id": expected_chat_id,
                "reason_ignored": "invalid_chat_id",
                "completed": False,
            },
        )
        return CashoutReactionCompletionResult(
            completed=False,
            cashout_id=None,
            reason="invalid_chat_id",
            reaction_emoji=reaction_emoji,
        )

    if not chat_ids_equivalent(normalized_chat, normalized_expected):
        logger.info(
            "reaction_update_ignored",
            extra={
                "telegram_message_id": telegram_message_id,
                "telegram_chat_id": normalized_chat,
                "expected_telegram_chat_id": normalized_expected,
                "matched_cashout": False,
                "completed": False,
                "reason_ignored": "different_chat",
                "reaction_emoji": reaction_emoji,
            },
        )
        return CashoutReactionCompletionResult(
            completed=False,
            cashout_id=None,
            reason="different_chat",
            matched_cashout=False,
            reaction_emoji=reaction_emoji,
        )

    async with SessionFactory() as session, session.begin():
        repository = CashoutRepository(session)
        cashout = await repository.get_by_telegram_message_for_update(
            telegram_message_id=telegram_message_id,
            telegram_chat_id=normalized_chat,
            fallback_chat_id=normalized_expected,
        )
        if cashout is None:
            logger.info(
                "reaction_mapping_failed",
                extra={
                    "telegram_message_id": telegram_message_id,
                    "telegram_chat_id": normalized_chat,
                    "matched_cashout": False,
                    "completed": False,
                    "reason_ignored": "no_matching_cashout",
                    "reaction_emoji": reaction_emoji,
                },
            )
            return CashoutReactionCompletionResult(
                completed=False,
                cashout_id=None,
                reason="no_matching_cashout",
                matched_cashout=False,
                reaction_emoji=reaction_emoji,
            )

        logger.info(
            "reaction_message_matched",
            extra={
                "cashout_request_id": cashout.id,
                "telegram_message_id": telegram_message_id,
                "telegram_chat_id": normalized_chat,
                "reaction_emoji": reaction_emoji,
            },
        )

        # Backfill missing chat ID when historical rows only stored message id.
        if cashout.telegram_chat_id is None:
            cashout.telegram_chat_id = normalized_chat

        previous_status = cashout.status.value
        if cashout.status == CashoutStatus.COMPLETED:
            logger.info(
                "reaction_update_ignored",
                extra={
                    "telegram_message_id": telegram_message_id,
                    "cashout_request_id": cashout.id,
                    "telegram_chat_id": normalized_chat,
                    "matched_cashout": True,
                    "previous_status": previous_status,
                    "completed": False,
                    "reason_ignored": "already_completed",
                    "reaction_emoji": reaction_emoji,
                },
            )
            return CashoutReactionCompletionResult(
                completed=False,
                cashout_id=cashout.id,
                reason="already_completed",
                matched_cashout=True,
                previous_status=previous_status,
                reaction_emoji=reaction_emoji,
            )
        if cashout.status == CashoutStatus.CANCELLED:
            logger.info(
                "reaction_update_ignored",
                extra={
                    "telegram_message_id": telegram_message_id,
                    "cashout_request_id": cashout.id,
                    "telegram_chat_id": normalized_chat,
                    "matched_cashout": True,
                    "previous_status": previous_status,
                    "completed": False,
                    "reason_ignored": "cancelled",
                    "reaction_emoji": reaction_emoji,
                },
            )
            return CashoutReactionCompletionResult(
                completed=False,
                cashout_id=cashout.id,
                reason="cancelled",
                matched_cashout=True,
                previous_status=previous_status,
                reaction_emoji=reaction_emoji,
            )

        now = datetime.now(UTC)
        # Database-level guard: only one concurrent transition wins.
        transition = await session.execute(
            update(CashoutRequest)
            .where(
                CashoutRequest.id == cashout.id,
                CashoutRequest.status.in_(
                    (
                        CashoutStatus.PENDING,
                        CashoutStatus.SENT,
                        CashoutStatus.FAILED_TO_SEND,
                    )
                ),
            )
            .values(
                status=CashoutStatus.COMPLETED,
                completed_at=now,
                telegram_next_attempt_at=None,
                telegram_chat_id=normalized_chat,
            )
            .returning(CashoutRequest.id)
        )
        transitioned_id = transition.scalar_one_or_none()
        if transitioned_id is None:
            logger.info(
                "reaction_update_ignored",
                extra={
                    "telegram_message_id": telegram_message_id,
                    "cashout_request_id": cashout.id,
                    "telegram_chat_id": normalized_chat,
                    "matched_cashout": True,
                    "previous_status": previous_status,
                    "completed": False,
                    "reason_ignored": "already_completed",
                    "reaction_emoji": reaction_emoji,
                },
            )
            return CashoutReactionCompletionResult(
                completed=False,
                cashout_id=cashout.id,
                reason="already_completed",
                matched_cashout=True,
                previous_status=previous_status,
                reaction_emoji=reaction_emoji,
            )

        await session.refresh(cashout)
        await repository.add_audit(
            CashoutRequestAudit(
                cashout_request_id=cashout.id,
                action=CashoutAuditAction.TELEGRAM_REACTION_COMPLETED,
                actor_user_id=None,
                previous_value={
                    "status": previous_status,
                    "completed_at": None,
                },
                new_value={
                    "status": CashoutStatus.COMPLETED.value,
                    "completed_at": now.isoformat(),
                    "telegram_message_id": telegram_message_id,
                    "telegram_chat_id": normalized_chat,
                    "reaction_emoji": reaction_emoji,
                    "reactor_telegram_user_id": reactor_user_id,
                    "source": source,
                },
            )
        )
        cashout_id = cashout.id

    await event_broker.publish(
        LiveEventType.CASHOUT_COMPLETED,
        cashout_id=cashout_id,
        broadcast=True,
    )
    await event_broker.publish(LiveEventType.LEDGER_CHANGED, broadcast=True)
    logger.info(
        "reaction_db_updated",
        extra={
            "cashout_request_id": cashout_id,
            "telegram_message_id": telegram_message_id,
            "telegram_chat_id": normalized_chat,
            "reaction_emoji": reaction_emoji,
            "previous_status": previous_status,
            "completed": True,
        },
    )
    logger.info(
        "reaction_notify_sent",
        extra={
            "cashout_request_id": cashout_id,
            "telegram_message_id": telegram_message_id,
            "sse_event": "cashout_completed",
        },
    )
    logger.info(
        "reaction_completion_detected",
        extra={
            "cashout_request_id": cashout_id,
            "telegram_message_id": telegram_message_id,
            "telegram_chat_id": normalized_chat,
            "reaction_emoji": reaction_emoji,
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
        reaction_emoji=reaction_emoji,
    )


def message_has_completion_reaction(
    message: object | None,
    allowed_reactions: frozenset[str] | None,
) -> bool:
    """Return True when a Telegram message currently has a completion reaction."""
    if message is None:
        return False
    if not _message_has_active_reaction(message):
        return False
    emoticons = extract_reaction_emoticons(getattr(message, "reactions", None))
    return reaction_matches_completion(emoticons, allowed_reactions)


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

"""Periodic cashout reaction reconciliation safety net.

Real-time Telethon reaction updates are the primary path. This loop catches
reactions missed during listener downtime, update gaps, or intermittent
delivery failures.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from telethon import errors  # type: ignore[import-untyped]

from app.core.logging import get_logger
from app.db.repositories.cashout import CashoutRepository
from app.db.session import SessionFactory
import app.telegram.listener_health as listener_health
from app.telegram.cashout_reactions import (
    CashoutReactionCompletionResult,
    complete_cashout_from_reaction,
    message_has_completion_reaction,
)
from app.telegram.peer_ids import normalize_telegram_chat_id

logger = get_logger(__name__)
TerminalReporter = Callable[[str], None]

DEFAULT_RECONCILIATION_INTERVAL_SECONDS = 20
DEFAULT_BATCH_SIZE = 40


@dataclass(frozen=True, slots=True)
class ReconciliationCandidate:
    cashout_id: int
    telegram_message_id: int
    telegram_chat_id: int | None


async def run_cashout_reaction_reconciliation_loop(
    client: Any,
    group: object,
    *,
    expected_chat_id: int,
    allowed_reactions: frozenset[str] | None,
    interval_seconds: float = DEFAULT_RECONCILIATION_INTERVAL_SECONDS,
    batch_size: int = DEFAULT_BATCH_SIZE,
    report: TerminalReporter = print,
) -> None:
    """Reconcile pending/sent cashout reactions on a fixed interval."""
    backoff_seconds = interval_seconds
    logger.info(
        "cashout_reaction_reconciliation_loop_started",
        extra={
            "telegram_chat_id": expected_chat_id,
            "interval_seconds": interval_seconds,
            "batch_size": batch_size,
        },
    )
    try:
        while True:
            try:
                results = await reconcile_pending_cashout_reactions(
                    client,
                    group,
                    expected_chat_id=expected_chat_id,
                    allowed_reactions=allowed_reactions,
                    limit=batch_size,
                )
                completed = sum(1 for item in results if item.completed)
                if completed:
                    report(
                        f"Reconciliation completed {completed} cashout(s) from Telegram reactions."
                    )
                listener_health.mark_reconciliation(error=None)
                backoff_seconds = interval_seconds
            except errors.FloodWaitError as flood:
                wait_for = int(getattr(flood, "seconds", 30) or 30)
                logger.warning(
                    "cashout_reaction_reconciliation_flood_wait",
                    extra={
                        "telegram_chat_id": expected_chat_id,
                        "wait_seconds": wait_for,
                    },
                )
                listener_health.mark_reconciliation(
                    error=f"FloodWait:{wait_for}s",
                )
                await asyncio.sleep(wait_for)
                continue
            except asyncio.CancelledError:
                raise
            except Exception as error:
                logger.exception(
                    "cashout_reaction_reconciliation_failed",
                    extra={"telegram_chat_id": expected_chat_id},
                )
                listener_health.mark_reconciliation(error=str(error)[:500])
                backoff_seconds = min(300.0, backoff_seconds * 2)

            await asyncio.sleep(backoff_seconds)
    finally:
        logger.info("cashout_reaction_reconciliation_loop_stopped")


async def reconcile_pending_cashout_reactions(
    client: Any,
    group: object,
    *,
    expected_chat_id: int,
    allowed_reactions: frozenset[str] | None,
    limit: int = DEFAULT_BATCH_SIZE,
) -> list[CashoutReactionCompletionResult]:
    """Fetch Telegram reactions for open cashouts and complete matches."""
    candidates = await _list_candidates(limit=limit)
    normalized_expected = normalize_telegram_chat_id(expected_chat_id)
    logger.info(
        "cashout_reaction_reconciliation_started",
        extra={
            "telegram_chat_id": normalized_expected,
            "total": len(candidates),
            "limit": limit,
        },
    )
    results: list[CashoutReactionCompletionResult] = []
    for candidate in candidates:
        chat_id = normalize_telegram_chat_id(candidate.telegram_chat_id) or (
            normalized_expected
        )
        if chat_id != normalized_expected:
            logger.info(
                "reaction_update_ignored",
                extra={
                    "cashout_request_id": candidate.cashout_id,
                    "telegram_message_id": candidate.telegram_message_id,
                    "telegram_chat_id": chat_id,
                    "expected_telegram_chat_id": normalized_expected,
                    "reason_ignored": "different_chat",
                    "completed": False,
                },
            )
            continue

        try:
            message = await client.get_messages(
                group,
                ids=candidate.telegram_message_id,
            )
        except errors.FloodWaitError:
            raise
        except Exception:
            logger.exception(
                "cashout_reaction_reconciliation_fetch_failed",
                extra={
                    "cashout_request_id": candidate.cashout_id,
                    "telegram_message_id": candidate.telegram_message_id,
                    "telegram_chat_id": chat_id,
                },
            )
            continue

        if not message_has_completion_reaction(message, allowed_reactions):
            logger.info(
                "reaction_update_ignored",
                extra={
                    "cashout_request_id": candidate.cashout_id,
                    "telegram_message_id": candidate.telegram_message_id,
                    "telegram_chat_id": chat_id,
                    "reason_ignored": "no_completion_reaction",
                    "completed": False,
                },
            )
            continue

        result = await complete_cashout_from_reaction(
            candidate.telegram_message_id,
            chat_id,
            expected_chat_id,
            allowed_reactions=allowed_reactions,
            source="reconciliation",
        )
        results.append(result)

    logger.info(
        "cashout_reaction_reconciliation_finished",
        extra={
            "telegram_chat_id": normalized_expected,
            "total": len(candidates),
            "completed": sum(1 for item in results if item.completed),
        },
    )
    return results


async def _list_candidates(limit: int) -> list[ReconciliationCandidate]:
    async with SessionFactory() as session:
        repository = CashoutRepository(session)
        rows = await repository.list_reaction_candidates(limit=limit)
    return [
        ReconciliationCandidate(
            cashout_id=row.cashout_id,
            telegram_message_id=row.telegram_message_id,
            telegram_chat_id=row.telegram_chat_id,
        )
        for row in rows
    ]

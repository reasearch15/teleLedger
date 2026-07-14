from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from app.core.logging import get_logger
from app.db.repositories.telegram_backfill_checkpoint import (
    TelegramBackfillCheckpointRepository,
)
from app.db.session import SessionFactory
from app.telegram.identity import telegram_display_name, telegram_entity_id
from app.telegram.inquiry_ingestion import ingest_inquiry_telegram_message
from app.telegram.inquiry_message_parser import InquiryMessageNotVisibleError

logger = get_logger(__name__)
TerminalReporter = Callable[[str], None]


class InquiryBackfillClient(Protocol):
    """Minimal Telethon client contract needed for inquiry backfill."""

    def iter_messages(
        self,
        entity: object,
        *,
        limit: int | None,
        min_id: int = 0,
    ) -> AsyncIterator[Any]:
        """Iterate recent messages from one Telegram entity."""
        ...


@dataclass(frozen=True, slots=True)
class InquiryBackfillSummary:
    messages_scanned: int = 0
    messages_stored: int = 0
    messages_updated: int = 0
    messages_ignored: int = 0
    messages_fetched: int = 0
    highest_scanned_message_id: int | None = None


@dataclass(frozen=True, slots=True)
class InquiryStartupBackfillSummary:
    mode: str
    last_checkpoint: int | None
    checkpoint_updated: bool
    backfill: InquiryBackfillSummary


async def backfill_inquiry_messages(
    client: InquiryBackfillClient,
    group: object,
    *,
    limit: int | None,
    report: TerminalReporter = print,
    since_message_id: int | None = None,
) -> InquiryBackfillSummary:
    """Fetch and idempotently ingest recent cashout-group inquiry messages."""
    report("Inquiry backfill started")
    report(
        f"Configured group: {telegram_display_name(group)} "
        f"({telegram_entity_id(group)})"
    )
    report(f"Limit: {limit if limit is not None else 'all'}")
    if since_message_id is not None:
        report(f"Since message ID: {since_message_id}")

    scanned = 0
    stored = 0
    updated = 0
    ignored = 0
    highest_scanned_message_id: int | None = None

    fetched_messages = [
        message
        async for message in client.iter_messages(
            group,
            limit=limit,
            min_id=since_message_id or 0,
        )
    ]

    for message in sorted(fetched_messages, key=lambda item: item.id):
        scanned += 1
        highest_scanned_message_id = message.id
        try:
            result = await ingest_inquiry_telegram_message(message, client=client)
        except InquiryMessageNotVisibleError:
            ignored += 1
            continue
        except Exception:
            logger.exception(
                "inquiry_backfill_message_failed",
                extra={"telegram_message_id": getattr(message, "id", None)},
            )
            ignored += 1
            continue
        if result.inserted:
            stored += 1
        else:
            updated += 1

    summary = InquiryBackfillSummary(
        messages_scanned=scanned,
        messages_stored=stored,
        messages_updated=updated,
        messages_ignored=ignored,
        messages_fetched=len(fetched_messages),
        highest_scanned_message_id=highest_scanned_message_id,
    )
    report(f"Inquiry messages fetched: {summary.messages_fetched}")
    report(f"Inquiry messages scanned: {summary.messages_scanned}")
    report(f"Inquiry messages stored: {summary.messages_stored}")
    report(f"Inquiry messages updated: {summary.messages_updated}")
    report(f"Inquiry messages ignored: {summary.messages_ignored}")
    report(
        "Highest scanned inquiry message ID: "
        f"{summary.highest_scanned_message_id or 'none'}"
    )
    report("Inquiry backfill completed")
    return summary


def _telegram_chat_id(group: object) -> int:
    entity_id = telegram_entity_id(group)
    if entity_id == "<unknown>":
        raise ValueError("Telegram group ID is unknown")
    return int(entity_id)


async def backfill_new_inquiry_messages(
    client: InquiryBackfillClient,
    group: object,
    *,
    limit: int,
    report: TerminalReporter = print,
) -> InquiryStartupBackfillSummary:
    """Run checkpoint-aware startup backfill for one cashout Telegram group."""
    telegram_chat_id = _telegram_chat_id(group)
    async with SessionFactory() as session:
        checkpoint = await TelegramBackfillCheckpointRepository(session).get(
            telegram_chat_id
        )

    last_checkpoint = (
        checkpoint.last_scanned_message_id if checkpoint is not None else None
    )
    mode = "incremental" if last_checkpoint is not None else "initial"
    report(f"Inquiry backfill mode: {mode}")
    report(
        "Inquiry last checkpoint: "
        f"{last_checkpoint if last_checkpoint is not None else 'none'}"
    )

    backfill = await backfill_inquiry_messages(
        client,
        group,
        limit=limit,
        report=report,
        since_message_id=last_checkpoint,
    )

    checkpoint_updated = False
    if backfill.highest_scanned_message_id is not None:
        async with SessionFactory.begin() as session:
            await TelegramBackfillCheckpointRepository(session).upsert(
                telegram_chat_id,
                backfill.highest_scanned_message_id,
            )
        checkpoint_updated = True

    report(f"Inquiry checkpoint updated: {'yes' if checkpoint_updated else 'no'}")
    return InquiryStartupBackfillSummary(
        mode=mode,
        last_checkpoint=last_checkpoint,
        checkpoint_updated=checkpoint_updated,
        backfill=backfill,
    )

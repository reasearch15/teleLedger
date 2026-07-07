from __future__ import annotations

import argparse
import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
from typing import Protocol

from app.core.config import get_settings
from app.core.logging import configure_logging
from app.db.repositories.telegram_backfill_checkpoint import (
    TelegramBackfillCheckpointRepository,
)
from app.db.session import SessionFactory
from app.schemas.telegram import IncomingTelegramMessage
from app.services.telegram_ingestion import (
    TelegramIngestionOutcome,
    TelegramIngestionResult,
)
from app.telegram.client import create_telegram_client
from app.telegram.diagnostics import report_ingestion_diagnostic
from app.telegram.identity import telegram_display_name, telegram_entity_id
from app.telegram.ingestion import ingest_telegram_message
from app.telegram.messages import TelegramMessageLike, convert_telegram_message

TerminalReporter = Callable[[str], None]
IngestMessage = Callable[
    [IncomingTelegramMessage],
    Awaitable[TelegramIngestionResult],
]


class BackfillClient(Protocol):
    """Minimal Telethon client contract needed for historical messages."""

    def iter_messages(
        self,
        entity: object,
        *,
        limit: int | None,
        min_id: int = 0,
    ) -> AsyncIterator[TelegramMessageLike]:
        """Iterate recent messages from one Telegram entity."""
        ...


@dataclass(frozen=True, slots=True)
class BackfillSummary:
    """Counters emitted after a historical Telegram scan."""

    messages_scanned: int = 0
    raw_messages_inserted: int = 0
    payments_created: int = 0
    duplicates_skipped: int = 0
    ignored_messages: int = 0
    messages_fetched: int = 0
    highest_scanned_message_id: int | None = None


@dataclass(frozen=True, slots=True)
class StartupBackfillSummary:
    """Checkpoint-aware startup backfill result."""

    mode: str
    last_checkpoint: int | None
    checkpoint_updated: bool
    backfill: BackfillSummary


async def backfill_messages(
    client: BackfillClient,
    group: object,
    *,
    limit: int | None,
    report: TerminalReporter = print,
    ingest_message: IngestMessage = ingest_telegram_message,
    since_message_id: int | None = None,
) -> BackfillSummary:
    """Fetch and idempotently ingest recent messages from a Telegram group."""
    report("Backfill started")
    report(
        f"Configured group: {telegram_display_name(group)} "
        f"({telegram_entity_id(group)})"
    )
    report(f"Limit: {limit if limit is not None else 'all'}")
    if since_message_id is not None:
        report(f"Since message ID: {since_message_id}")

    scanned = 0
    inserted = 0
    payments = 0
    duplicates = 0
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
        if not message.raw_text:
            ignored += 1
            highest_scanned_message_id = message.id
            continue

        incoming = await convert_telegram_message(message)
        result = await ingest_message(incoming)
        report_ingestion_diagnostic(incoming, result, report)
        highest_scanned_message_id = message.id

        if result.raw_message_inserted:
            inserted += 1
        if result.payment_inserted:
            payments += 1
        if result.outcome == TelegramIngestionOutcome.DUPLICATE:
            duplicates += 1
        elif not result.parser_matched:
            ignored += 1

    summary = BackfillSummary(
        messages_scanned=scanned,
        raw_messages_inserted=inserted,
        payments_created=payments,
        duplicates_skipped=duplicates,
        ignored_messages=ignored,
        messages_fetched=len(fetched_messages),
        highest_scanned_message_id=highest_scanned_message_id,
    )
    report(f"Messages fetched: {summary.messages_fetched}")
    report(f"Messages scanned: {summary.messages_scanned}")
    report(f"Raw messages inserted: {summary.raw_messages_inserted}")
    report(f"Payments created: {summary.payments_created}")
    report(f"Duplicates skipped: {summary.duplicates_skipped}")
    report(f"Ignored non-payment messages: {summary.ignored_messages}")
    report(
        "Highest scanned message ID: "
        f"{summary.highest_scanned_message_id or 'none'}"
    )
    report("Backfill completed")
    return summary


def _telegram_chat_id(group: object) -> int:
    entity_id = telegram_entity_id(group)
    if entity_id == "<unknown>":
        raise ValueError("Telegram group ID is unknown")
    return int(entity_id)


async def backfill_new_messages(
    client: BackfillClient,
    group: object,
    *,
    limit: int,
    report: TerminalReporter = print,
    ingest_message: IngestMessage = ingest_telegram_message,
) -> StartupBackfillSummary:
    """Run checkpoint-aware startup backfill for one Telegram group."""
    telegram_chat_id = _telegram_chat_id(group)
    async with SessionFactory() as session:
        checkpoint = await TelegramBackfillCheckpointRepository(session).get(
            telegram_chat_id
        )

    last_checkpoint = (
        checkpoint.last_scanned_message_id if checkpoint is not None else None
    )
    mode = "incremental" if last_checkpoint is not None else "initial"
    report(f"Backfill mode: {mode}")
    report(
        "Last checkpoint: "
        f"{last_checkpoint if last_checkpoint is not None else 'none'}"
    )

    backfill = await backfill_messages(
        client,
        group,
        limit=limit,
        report=report,
        ingest_message=ingest_message,
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

    report(f"Checkpoint updated: {'yes' if checkpoint_updated else 'no'}")
    return StartupBackfillSummary(
        mode=mode,
        last_checkpoint=last_checkpoint,
        checkpoint_updated=checkpoint_updated,
        backfill=backfill,
    )


@dataclass(frozen=True, slots=True)
class ManualBackfillOptions:
    """CLI options for a one-off Telegram history repair."""

    limit: int | None
    since_message_id: int | None


def parse_args() -> ManualBackfillOptions:
    """Parse manual backfill command-line options."""
    parser = argparse.ArgumentParser(description="Backfill Telegram group messages.")
    parser.add_argument(
        "--limit",
        type=int,
        default=500,
        help="Maximum number of recent messages to scan.",
    )
    parser.add_argument(
        "--full",
        action="store_true",
        help="Scan full available history instead of applying a limit.",
    )
    parser.add_argument(
        "--since-message-id",
        type=int,
        default=None,
        help="Only scan Telegram messages newer than this message ID.",
    )
    args = parser.parse_args()
    return ManualBackfillOptions(
        limit=None if args.full else args.limit,
        since_message_id=args.since_message_id,
    )


async def run_manual_backfill(
    report: TerminalReporter = print,
    options: ManualBackfillOptions | None = None,
) -> BackfillSummary:
    """Connect with the existing session, backfill once, and disconnect."""
    settings = get_settings()
    configure_logging(settings.log_level)
    options = options or ManualBackfillOptions(
        limit=settings.telegram_backfill_limit,
        since_message_id=None,
    )
    group_target = settings.telegram_group_target
    if group_target is None:
        raise RuntimeError(
            "TELEGRAM_GROUP_ID or TELEGRAM_GROUP_USERNAME is required for backfill"
        )

    client = create_telegram_client(settings)
    try:
        await client.start()
        group = await client.get_entity(group_target)
        return await backfill_messages(
            client,
            group,
            limit=options.limit,
            report=report,
            since_message_id=options.since_message_id,
        )
    finally:
        await client.disconnect()


def main() -> None:
    """CLI entry point."""
    try:
        asyncio.run(run_manual_backfill(options=parse_args()))
    except KeyboardInterrupt:
        print("Backfill stopped.")


if __name__ == "__main__":
    main()

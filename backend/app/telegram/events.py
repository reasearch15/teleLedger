from __future__ import annotations

from collections.abc import Awaitable, Callable, Iterable
from typing import Protocol

from telethon import utils  # type: ignore[import-untyped]

from app.core.logging import get_logger
from app.schemas.telegram import IncomingTelegramMessage
from app.services.telegram_ingestion import TelegramIngestionResult
from app.telegram.cashout_reactions import (
    CashoutReactionCompletionResult,
    complete_cashout_from_reaction,
)
from app.telegram.diagnostics import report_ingestion_diagnostic
from app.telegram.messages import TelegramMessageLike, convert_telegram_message

logger = get_logger(__name__)


class TelegramNewMessageEvent(TelegramMessageLike, Protocol):
    """Structural event contract used for conversion and easy mocking."""


class TelegramReactionEvent(Protocol):
    """Structural contract for Telethon raw message reaction updates."""

    msg_id: int
    peer: object
    reactions: object | None


IngestMessage = Callable[[IncomingTelegramMessage], Awaitable[TelegramIngestionResult]]
CompleteCashoutFromReaction = Callable[
    [int],
    Awaitable[CashoutReactionCompletionResult],
]
CompleteRecentReactions = Callable[[], Awaitable[list[CashoutReactionCompletionResult]]]
EventHandler = Callable[[TelegramNewMessageEvent], Awaitable[None]]
ReactionEventHandler = Callable[[object], Awaitable[None]]
TerminalReporter = Callable[[str], None]


def _message_preview(raw_text: str, limit: int = 90) -> str:
    preview = " ".join(raw_text.split())
    if len(preview) <= limit:
        return preview
    return f"{preview[: limit - 1]}…"


def create_new_message_handler(
    ingest_message: IngestMessage,
    report: TerminalReporter = print,
) -> EventHandler:
    """Build a Telethon handler around an injected ingestion use case."""

    async def handle_new_message(event: TelegramNewMessageEvent) -> None:
        if not event.raw_text:
            report(f"Message {event.id}: ignored (non-text message)")
            logger.info(
                "telegram_message_ignored",
                extra={
                    "telegram_message_id": event.id,
                    "telegram_chat_id": event.chat_id,
                    "outcome": "non_text",
                },
            )
            return

        report(f"Message {event.id}: {_message_preview(event.raw_text)}")
        logger.info(
            "telegram_message_received",
            extra={
                "telegram_message_id": event.id,
                "telegram_chat_id": event.chat_id,
            },
        )

        try:
            incoming = await convert_telegram_message(event)
            result = await ingest_message(incoming)
        except Exception:
            report(f"Message {event.id}: processing failed; see logs")
            logger.exception(
                "telegram_message_processing_failed",
                extra={"telegram_message_id": event.id},
            )
            return

        report_ingestion_diagnostic(incoming, result, report)
        log_messages = {
            "parsed": "telegram_payment_parsed",
            "ignored": "telegram_message_ignored",
            "duplicate": "telegram_duplicate_skipped",
        }
        logger.info(
            log_messages[result.outcome.value],
            extra={
                "telegram_message_id": incoming.telegram_message_id,
                "telegram_chat_id": incoming.telegram_chat_id,
                "outcome": result.outcome.value,
                "existing_raw_message": result.existing_raw_message,
                "existing_payment_event": result.existing_payment_event,
                "parser_matched": result.parser_matched,
                "raw_message_inserted": result.raw_message_inserted,
                "payment_inserted": result.payment_inserted,
                "reason_skipped": result.reason_skipped,
            },
        )

        if result.outcome.value == "parsed" and result.parsed_payment is not None:
            parsed = result.parsed_payment
            report(
                "Parsed payment: "
                f"amount=${parsed.amount} | "
                f"sender={parsed.payment_sender_name} | "
                f"recipient_tag={parsed.recipient_tag}"
            )
        elif result.outcome.value == "duplicate":
            report(f"Message {event.id}: duplicate skipped")
        else:
            report(f"Message {event.id}: ignored (not a payment)")

    return handle_new_message


def create_reaction_handler(
    *,
    expected_chat_id: int,
    complete_from_reaction: CompleteCashoutFromReaction = (complete_cashout_from_reaction),
    complete_recent_reactions: CompleteRecentReactions | None = None,
    report: TerminalReporter = print,
) -> ReactionEventHandler:
    """Build a Telethon raw update handler for cashout message reactions."""

    async def handle_reaction(event: object) -> None:
        message_id = _reaction_message_id(event)
        chat_id = _reaction_chat_id(event)
        raw_update_type = type(event).__name__
        reaction_summary = _reaction_summary(event)
        logger.info(
            "telegram_raw_update_received",
            extra={
                "telegram_message_id": message_id,
                "telegram_chat_id": chat_id,
                "expected_telegram_chat_id": expected_chat_id,
                "raw_update_type": raw_update_type,
                "reaction_summary": reaction_summary,
            },
        )
        if "Reaction" in raw_update_type:
            logger.info(
                "telegram_reaction_raw_update_received",
                extra={
                    "telegram_message_id": message_id,
                    "telegram_chat_id": chat_id,
                    "expected_telegram_chat_id": expected_chat_id,
                    "raw_update_type": raw_update_type,
                    "reaction_summary": reaction_summary,
                },
            )
        if raw_update_type == "UpdateRecentReactions":
            logger.info(
                "telegram_recent_reactions_update_received",
                extra={
                    "telegram_message_id": message_id,
                    "telegram_chat_id": chat_id,
                    "expected_telegram_chat_id": expected_chat_id,
                    "raw_update_type": raw_update_type,
                    "reaction_summary": reaction_summary,
                    "reason_ignored": (
                        "fieldless_update" if complete_recent_reactions is None else None
                    ),
                },
            )
            if complete_recent_reactions is not None:
                try:
                    results = await complete_recent_reactions()
                except Exception:
                    report("Recent reaction scan failed; see logs")
                    logger.exception(
                        "cashout_recent_reaction_scan_failed",
                        extra={"raw_update_type": raw_update_type},
                    )
                    return
                logger.info(
                    "cashout_recent_reaction_scan_processed",
                    extra={
                        "raw_update_type": raw_update_type,
                        "completed": any(result.completed for result in results),
                        "total": len(results),
                    },
                )
                for result in results:
                    if result.completed and result.cashout_id is not None:
                        report(f"Cashout {result.cashout_id}: completed by recent reaction")
            return
        has_active_reaction = _has_active_reaction_update(event)
        if not isinstance(message_id, int):
            return
        logger.info(
            "telegram_reaction_update_received",
            extra={
                "telegram_message_id": message_id,
                "telegram_chat_id": chat_id,
                "expected_telegram_chat_id": expected_chat_id,
                "raw_update_type": raw_update_type,
                "reaction_summary": reaction_summary,
            },
        )
        if chat_id != expected_chat_id:
            logger.info(
                "telegram_reaction_update_ignored",
                extra={
                    "telegram_message_id": message_id,
                    "telegram_chat_id": chat_id,
                    "expected_telegram_chat_id": expected_chat_id,
                    "raw_update_type": raw_update_type,
                    "reaction_summary": reaction_summary,
                    "completed": False,
                    "reason_ignored": "different_chat",
                },
            )
            return
        if not has_active_reaction:
            logger.info(
                "telegram_reaction_update_ignored",
                extra={
                    "telegram_message_id": message_id,
                    "telegram_chat_id": chat_id,
                    "expected_telegram_chat_id": expected_chat_id,
                    "raw_update_type": raw_update_type,
                    "reaction_summary": reaction_summary,
                    "completed": False,
                    "reason_ignored": "no_active_reaction",
                },
            )
            return

        try:
            result = await complete_from_reaction(message_id)
        except Exception:
            report(f"Reaction on message {message_id}: processing failed; see logs")
            logger.exception(
                "cashout_reaction_processing_failed",
                extra={"telegram_message_id": message_id},
            )
            return

        logger.info(
            "cashout_reaction_processed",
            extra={
                "telegram_message_id": message_id,
                "cashout_request_id": result.cashout_id,
                "telegram_chat_id": chat_id,
                "outcome": result.reason,
                "reaction_summary": reaction_summary,
                "matched_cashout": result.matched_cashout,
                "previous_status": result.previous_status,
                "completed": result.completed,
                "reason_ignored": None if result.completed else result.reason,
            },
        )
        if result.completed:
            report(f"Message {message_id}: cashout completed by reaction")

    return handle_reaction


def _reaction_chat_id(event: object) -> int | None:
    peer = getattr(event, "peer", None) or getattr(event, "peer_id", None)
    if peer is None:
        return None
    try:
        return int(utils.get_peer_id(peer))
    except Exception:
        logger.exception("cashout_reaction_peer_parse_failed")
        return None


def _reaction_message_id(event: object) -> int | None:
    for attribute in ("msg_id", "message_id", "id"):
        value = getattr(event, attribute, None)
        if isinstance(value, int):
            return value
    return None


def _has_active_reaction_update(event: object) -> bool:
    reactions = getattr(event, "reactions", None)
    if _has_active_reaction_collection(getattr(event, "new_reactions", None)):
        return True
    if _has_active_reaction_collection(getattr(event, "recent_reactions", None)):
        return True
    if _has_active_reaction(reactions):
        return True
    if isinstance(reactions, Iterable) and not isinstance(reactions, (str, bytes)):
        return _has_active_reaction_collection(reactions)
    if reactions is not None and not hasattr(reactions, "results"):
        return True
    if reactions is not None and _has_active_reaction_collection(
        getattr(reactions, "recent_reactions", None)
    ):
        return True
    return False


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


def _reaction_summary(event: object) -> str | None:
    parts: list[str] = []
    for attribute in (
        "old_reactions",
        "new_reactions",
        "recent_reactions",
        "reactions",
    ):
        value = getattr(event, attribute, None)
        if value is not None:
            parts.append(f"{attribute}={_summarize_reaction_value(value)}")
    return "; ".join(parts) or None


def _summarize_reaction_value(value: object) -> str:
    results = getattr(value, "results", None)
    if results is not None:
        return f"results:{_summarize_reaction_iterable(results)}"
    recent = getattr(value, "recent_reactions", None)
    if recent is not None:
        return f"recent:{_summarize_reaction_iterable(recent)}"
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes)):
        return _summarize_reaction_iterable(value)
    return type(value).__name__


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

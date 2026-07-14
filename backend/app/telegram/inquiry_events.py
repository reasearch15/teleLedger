from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from app.core.logging import get_logger
from app.telegram.inquiry_ingestion import (
    ingest_inquiry_telegram_message,
    mark_inquiry_message_deleted,
)
from app.telegram.inquiry_message_parser import InquiryMessageNotVisibleError

logger = get_logger(__name__)

TerminalReporter = Callable[[str], None]
InquiryIngest = Callable[[Any], Awaitable[None]]


def create_inquiry_message_handlers(
    *,
    ingest_message: InquiryIngest | None = None,
    report: TerminalReporter = print,
) -> tuple[Callable[[Any], Any], Callable[[Any], Any], Callable[[Any], Any]]:
    """Build handlers for cashout-group Telegram messages, edits, and deletes."""

    async def _ingest(message: Any) -> None:
        handler = ingest_message or _default_ingest
        await handler(message)

    async def handle_new_message(event: Any) -> None:
        try:
            await _ingest(event)
            report(f"Inquiry message {event.id}: stored")
        except InquiryMessageNotVisibleError as error:
            report(f"Inquiry message {event.id}: ignored ({error})")
            logger.info(
                "inquiry_message_ignored",
                extra={
                    "telegram_message_id": getattr(event, "id", None),
                    "reason": str(error),
                },
            )
        except ValueError as error:
            report(f"Inquiry message {event.id}: ignored ({error})")
            logger.info(
                "inquiry_message_ignored",
                extra={
                    "telegram_message_id": getattr(event, "id", None),
                    "reason": str(error),
                },
            )
        except Exception:
            report(f"Inquiry message {event.id}: processing failed; see logs")
            logger.exception(
                "inquiry_message_processing_failed",
                extra={"telegram_message_id": getattr(event, "id", None)},
            )

    async def handle_edited_message(event: Any) -> None:
        try:
            await _ingest(event)
            report(f"Inquiry message {event.id}: updated")
        except InquiryMessageNotVisibleError as error:
            report(f"Inquiry message {event.id}: edit ignored (no visible content)")
            logger.info(
                "inquiry_edit_ignored",
                extra={
                    "telegram_message_id": getattr(event, "id", None),
                    "telegram_chat_id": getattr(event, "chat_id", None),
                    "reason": str(error),
                },
            )
        except Exception:
            report(f"Inquiry message {event.id}: edit processing failed; see logs")
            logger.exception(
                "inquiry_message_edit_failed",
                extra={
                    "telegram_message_id": getattr(event, "id", None),
                    "telegram_chat_id": getattr(event, "chat_id", None),
                },
            )

    async def handle_deleted_messages(event: Any) -> None:
        chat_id = getattr(event, "chat_id", None)
        deleted_ids = getattr(event, "deleted_ids", None) or []
        if chat_id is None:
            return
        for message_id in deleted_ids:
            try:
                deleted = await mark_inquiry_message_deleted(
                    telegram_chat_id=int(chat_id),
                    telegram_message_id=int(message_id),
                )
                if deleted:
                    report(f"Inquiry message {message_id}: deleted")
            except Exception:
                logger.exception(
                    "inquiry_message_delete_failed",
                    extra={
                        "telegram_message_id": message_id,
                        "telegram_chat_id": chat_id,
                    },
                )

    return handle_new_message, handle_edited_message, handle_deleted_messages


async def _default_ingest(message: Any) -> None:
    client = getattr(message, "client", None) or getattr(message, "_client", None)
    await ingest_inquiry_telegram_message(message, client=client)

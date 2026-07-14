from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from telethon.tl.types import (  # type: ignore[import-untyped]
    DocumentAttributeFilename,
    DocumentAttributeImageSize,
    MessageMediaDocument,
    MessageMediaPhoto,
)

from app.core.logging import get_logger
from app.telegram.inquiry_media import normalize_image_mime_type

logger = get_logger(__name__)


class InquiryMessageNotVisibleError(ValueError):
    """Raised when a Telegram payload has no Inquiry-visible content."""


@dataclass(frozen=True, slots=True)
class ParsedInquiryTelegramMessage:
    telegram_chat_id: int
    telegram_message_id: int
    telegram_sender_id: int | None
    sender_display_name: str | None
    sender_username: str | None
    text: str | None
    caption: str | None
    message_date: datetime
    edited_at: datetime | None
    is_outbound: bool
    media_type: str
    media_mime_type: str | None
    media_filename: str | None
    has_downloadable_media: bool


def is_cashout_panel_message_text(text: str | None) -> bool:
    """Detect workflow messages produced by the Cashout panel formatter."""
    if not text:
        return False
    normalized = text.strip()
    return normalized.startswith("🔴 CASHOUT REQUEST")


async def parse_inquiry_telegram_message(message: Any) -> ParsedInquiryTelegramMessage:
    """Extract inquiry-relevant fields from a live or historical Telethon message."""
    if message.chat_id is None:
        raise ValueError("Telegram message does not contain a chat ID")

    message_date = message.date
    if message_date.tzinfo is None:
        message_date = message_date.replace(tzinfo=UTC)

    edited_at = getattr(message, "edit_date", None)
    if isinstance(edited_at, datetime) and edited_at.tzinfo is None:
        edited_at = edited_at.replace(tzinfo=UTC)

    sender = await message.get_sender()
    sender_display_name = _sender_display_name(sender)
    sender_username = _sender_username(sender)
    text = getattr(message, "message", None) or getattr(message, "raw_text", None)
    text = text if isinstance(text, str) and text.strip() else None
    caption = None
    media_type = "none"
    media_mime_type = None
    media_filename = None
    has_downloadable_media = False

    media = getattr(message, "media", None)
    if isinstance(media, MessageMediaPhoto):
        media_type = "photo"
        media_mime_type = "image/jpeg"
        has_downloadable_media = True
        caption = text
        text = None
    elif isinstance(media, MessageMediaDocument):
        document = getattr(media, "document", None)
        raw_mime_type = getattr(document, "mime_type", None)
        media_filename = _document_filename(document)
        normalized_mime = normalize_image_mime_type(
            raw_mime_type if isinstance(raw_mime_type, str) else None,
            filename=media_filename,
        )
        if normalized_mime is not None or _document_is_image(document):
            media_type = "document"
            media_mime_type = normalized_mime or "image/jpeg"
            has_downloadable_media = True
            caption = text
            text = None

    if text is None and caption is None and not has_downloadable_media:
        raise InquiryMessageNotVisibleError(
            "Telegram message has no inquiry-visible content"
        )

    parsed = ParsedInquiryTelegramMessage(
        telegram_chat_id=int(message.chat_id),
        telegram_message_id=int(message.id),
        telegram_sender_id=getattr(message, "sender_id", None),
        sender_display_name=sender_display_name,
        sender_username=sender_username,
        text=text,
        caption=caption,
        message_date=message_date,
        edited_at=edited_at,
        is_outbound=bool(getattr(message, "out", False)),
        media_type=media_type,
        media_mime_type=media_mime_type,
        media_filename=media_filename,
        has_downloadable_media=has_downloadable_media,
    )
    logger.info(
        "inquiry_message_parsed",
        extra={
            "telegram_message_id": parsed.telegram_message_id,
            "telegram_chat_id": parsed.telegram_chat_id,
            "grouped_id": getattr(message, "grouped_id", None),
            "message_type": parsed.media_type,
            "caption": parsed.caption,
            "has_photo": isinstance(media, MessageMediaPhoto),
            "has_document": isinstance(media, MessageMediaDocument),
            "mime_type": parsed.media_mime_type,
            "file_extension": Path(media_filename or "").suffix.lower() or None,
            "has_downloadable_media": parsed.has_downloadable_media,
        },
    )
    return parsed


def _document_is_image(document: object | None) -> bool:
    if document is None:
        return False
    for attribute in getattr(document, "attributes", []) or []:
        if isinstance(attribute, DocumentAttributeImageSize):
            return True
    return False


def _sender_display_name(sender: object | None) -> str | None:
    if sender is None:
        return None
    title = getattr(sender, "title", None)
    if isinstance(title, str) and title.strip():
        return title.strip()
    parts: list[str] = []
    for attribute in ("first_name", "last_name"):
        value = getattr(sender, attribute, None)
        if isinstance(value, str) and value.strip():
            parts.append(value.strip())
    if parts:
        return " ".join(parts)
    username = getattr(sender, "username", None)
    if isinstance(username, str) and username.strip():
        return username.strip()
    return None


def _sender_username(sender: object | None) -> str | None:
    if sender is None:
        return None
    username = getattr(sender, "username", None)
    if isinstance(username, str) and username.strip():
        return username.strip()
    return None


def _document_filename(document: object | None) -> str | None:
    if document is None:
        return None
    for attribute in getattr(document, "attributes", []) or []:
        if isinstance(attribute, DocumentAttributeFilename):
            filename = getattr(attribute, "file_name", None)
            if isinstance(filename, str) and filename.strip():
                return filename.strip()
    return None

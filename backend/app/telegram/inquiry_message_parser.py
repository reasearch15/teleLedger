from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from telethon.tl.types import (  # type: ignore[import-untyped]
    DocumentAttributeFilename,
    MessageMediaDocument,
    MessageMediaPhoto,
)

from app.telegram.inquiry_media import ALLOWED_IMAGE_MIME_TYPES


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
        mime_type = getattr(document, "mime_type", None)
        if isinstance(mime_type, str) and mime_type in ALLOWED_IMAGE_MIME_TYPES:
            media_type = "document"
            media_mime_type = mime_type
            has_downloadable_media = True
            caption = text
            text = None
            media_filename = _document_filename(document)

    if text is None and caption is None and not has_downloadable_media:
        raise InquiryMessageNotVisibleError(
            "Telegram message has no inquiry-visible content"
        )

    return ParsedInquiryTelegramMessage(
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

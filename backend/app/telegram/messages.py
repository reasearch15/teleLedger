from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from app.schemas.telegram import IncomingTelegramMessage


class TelegramMessageLike(Protocol):
    """Common fields exposed by live events and historical Telethon messages."""

    id: int
    date: datetime
    chat_id: int | None
    sender_id: int | None
    raw_text: str | None

    async def get_sender(self) -> object | None:
        """Return the sender entity, if available."""
        ...


def sender_display_name(sender: object | None) -> str | None:
    """Extract a safe display name from a Telegram sender entity."""
    if sender is None:
        return None

    title = getattr(sender, "title", None)
    if isinstance(title, str) and title.strip():
        return title.strip()

    name_parts: list[str] = []
    for attribute in ("first_name", "last_name"):
        value = getattr(sender, attribute, None)
        if isinstance(value, str) and value.strip():
            name_parts.append(value.strip())
    if name_parts:
        return " ".join(name_parts)

    username = getattr(sender, "username", None)
    if isinstance(username, str) and username.strip():
        return username.strip()
    return None


async def convert_telegram_message(
    message: TelegramMessageLike,
) -> IncomingTelegramMessage:
    """Convert a live or historical Telegram message for shared ingestion."""
    if message.chat_id is None:
        raise ValueError("Telegram message does not contain a chat ID")
    if not message.raw_text:
        raise ValueError("Telegram message does not contain text")

    received_at = message.date
    if received_at.tzinfo is None:
        received_at = received_at.replace(tzinfo=UTC)

    sender = await message.get_sender()
    return IncomingTelegramMessage(
        telegram_chat_id=message.chat_id,
        telegram_message_id=message.id,
        sender_id=message.sender_id,
        sender_name=sender_display_name(sender),
        raw_text=message.raw_text,
        received_at=received_at,
    )


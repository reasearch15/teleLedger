from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class IncomingTelegramMessage(BaseModel):
    """Transport-neutral representation of an incoming Telegram message."""

    model_config = ConfigDict(frozen=True)

    telegram_chat_id: int
    telegram_message_id: int = Field(gt=0)
    sender_id: int | None = None
    sender_name: str | None = Field(default=None, max_length=255)
    raw_text: str
    received_at: datetime

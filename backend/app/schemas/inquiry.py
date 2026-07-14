from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class InquiryMessageResponse(BaseModel):
    id: int
    telegram_chat_id: int
    telegram_message_id: int
    telegram_sender_id: int | None
    sender_display_name: str | None
    sender_username: str | None
    text: str | None
    caption: str | None
    message_date: datetime
    edited_at: datetime | None
    direction: str
    message_source: str
    media_type: str
    media_mime_type: str | None
    media_filename: str | None
    media_size_bytes: int | None
    media_download_status: str
    media_error: str | None
    has_media: bool
    telegram_grouped_id: int | None
    reply_to_telegram_message_id: int | None
    forward_from_display_name: str | None
    is_deleted: bool
    sent_by_teleledger_user_id: int | None
    sent_by_username: str | None
    starts_new_sender_block: bool
    is_edited: bool


class InquiryMessageListResponse(BaseModel):
    items: list[InquiryMessageResponse]
    pagination: dict[str, str | bool | None]
    has_more: bool
    next_cursor: str | None


class SendInquiryMessageRequest(BaseModel):
    text: str | None = Field(default=None, max_length=4000)
    idempotency_key: UUID


class SendInquiryMessageResponse(BaseModel):
    message: InquiryMessageResponse

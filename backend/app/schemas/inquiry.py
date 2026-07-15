from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, Field


class InquiryMessageResponse(BaseModel):
    id: int
    sender_alias: str | None
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
    has_album: bool
    is_reply: bool
    is_deleted: bool
    sent_by_name: str | None
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

from __future__ import annotations

import mimetypes
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from fastapi import UploadFile
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings, get_settings
from app.core.logging import get_logger
from app.db.repositories.inquiry_message import InquiryMessageRepository
from app.models.inquiry_message import (
    InquiryDirection,
    InquiryMediaDownloadStatus,
    InquiryMediaType,
    InquiryMessage,
    InquiryMessageSource,
)
from app.models.user import User, UserRole
from app.services.base import ApplicationService
from app.telegram.client import create_telegram_client
from app.telegram.inquiry_ingestion import ingest_inquiry_telegram_message
from app.telegram.inquiry_media import ALLOWED_IMAGE_MIME_TYPES
from app.telegram.peer_ids import normalize_telegram_chat_id
from app.websocket.events import LiveEventType, event_broker

logger = get_logger(__name__)


class InquiryAuthorizationError(Exception):
    """Raised when a user cannot access inquiry chat operations."""


class InquiryValidationError(Exception):
    """Raised when an inquiry send request is invalid."""


class InquiryNotFoundError(Exception):
    """Raised when an inquiry message cannot be found."""


class InquiryService(ApplicationService):
    """Inquiry chat listing, grouping, media access, and outbound send."""

    def __init__(self, session: AsyncSession, settings: Settings | None = None) -> None:
        self._session = session
        self._settings = settings or get_settings()
        self._repository = InquiryMessageRepository(session)

    async def list_messages(
        self,
        *,
        actor: User,
        limit: int,
        cursor: str | None,
        before_message_id: int | None = None,
    ) -> tuple[
        list[InquiryMessage],
        dict[str, bool | str | None],
        dict[int, str | None],
        dict[int, str],
    ]:
        self._require_viewer(actor)
        chat_id = self._cashout_chat_id()
        before_cursor = await self._cursor_from_before_message_id(
            chat_id=chat_id,
            before_message_id=before_message_id,
        )
        if before_cursor is None:
            before_cursor = self._parse_cursor(cursor)
        page = await self._repository.list_visible_messages(
            telegram_chat_id=chat_id,
            limit=limit,
            before_cursor=before_cursor,
        )
        chronological = list(reversed(page.items))
        aliases = await self._repository.aliases_for_messages(chronological)
        if aliases:
            await self._session.commit()
        usernames = await self._sender_usernames(chronological)
        return chronological, {
            "hasMore": page.has_more,
            "nextCursor": page.next_cursor,
        }, usernames, aliases

    async def compute_sender_block_flags(
        self,
        messages: list[InquiryMessage],
    ) -> dict[int, bool]:
        flags: dict[int, bool] = {}
        previous: InquiryMessage | None = None
        chat_id = self._cashout_chat_id()
        for message in messages:
            if previous is None:
                flags[message.id] = True
            else:
                flags[message.id] = await self._repository.has_visible_grouping_break(
                    telegram_chat_id=chat_id,
                    previous=previous,
                    current=message,
                )
            previous = message
        return flags

    async def get_message_for_media(
        self,
        *,
        actor: User,
        message_id: int,
    ) -> InquiryMessage:
        self._require_viewer(actor)
        message = await self._session.get(InquiryMessage, message_id)
        if message is None or message.message_source == InquiryMessageSource.CASHOUT_PANEL:
            raise InquiryNotFoundError("Inquiry message not found")
        if message.telegram_chat_id != self._cashout_chat_id():
            raise InquiryNotFoundError("Inquiry message not found")
        return message

    async def send_message(
        self,
        *,
        actor: User,
        text: str | None,
        idempotency_key: UUID,
        image: UploadFile | None = None,
    ) -> InquiryMessage:
        self._require_viewer(actor)
        cleaned_text = text.strip() if isinstance(text, str) and text.strip() else None
        if cleaned_text is None and image is None:
            raise InquiryValidationError("Text or image is required")

        existing = await self._repository.get_by_idempotency_key(str(idempotency_key))
        if existing is not None:
            return existing

        client = create_telegram_client(self._settings)
        chat_id = self._cashout_chat_id()
        try:
            await client.connect()
            if not await client.is_user_authorized():
                raise InquiryValidationError("Inquiry delivery session is not authorized")
            entity = await client.get_entity(chat_id)
            sent_message = await self._send_via_telegram(
                client,
                entity,
                text=cleaned_text,
                image=image,
            )
            result = await ingest_inquiry_telegram_message(
                sent_message,
                client=client,
                forced_source=InquiryMessageSource.INQUIRY,
                sent_by_teleledger_user_id=actor.id,
                idempotency_key=str(idempotency_key),
            )
            stored = await self._repository.get_by_telegram_identity(
                telegram_chat_id=chat_id,
                telegram_message_id=sent_message.id,
            )
            if stored is None:
                raise InquiryValidationError("Failed to persist sent inquiry message")
            await event_broker.publish(
                LiveEventType.INQUIRY_MESSAGE_CREATED,
                inquiry_message_id=stored.id,
            )
            logger.info(
                "inquiry_message_sent",
                extra={
                    "inquiry_message_id": stored.id,
                    "telegram_message_id": stored.telegram_message_id,
                    "actor_user_id": actor.id,
                    "inserted": result.inserted,
                },
            )
            return stored
        finally:
            await client.disconnect()

    def _require_viewer(self, actor: User) -> None:
        if actor.role not in (UserRole.ADMIN, UserRole.STAFF, UserRole.COADMIN):
            raise InquiryAuthorizationError("Staff access required")

    def _cashout_chat_id(self) -> int:
        chat_id = self._settings.telegram_cashout_group_id
        if chat_id is None:
            raise InquiryValidationError("Inquiry delivery channel is not configured")
        normalized = normalize_telegram_chat_id(chat_id)
        if normalized is None:
            raise InquiryValidationError("Inquiry delivery channel is not configured")
        return normalized

    @staticmethod
    def _parse_cursor(cursor: str | None) -> tuple[datetime, int] | None:
        if cursor is None:
            return None
        if "|" not in cursor:
            raise InquiryValidationError("Invalid pagination cursor")
        date_value, row_id = cursor.rsplit("|", 1)
        try:
            return datetime.fromisoformat(date_value), int(row_id)
        except ValueError as error:
            raise InquiryValidationError("Invalid pagination cursor") from error

    async def _cursor_from_before_message_id(
        self,
        *,
        chat_id: int,
        before_message_id: int | None,
    ) -> tuple[datetime, int] | None:
        if before_message_id is None:
            return None
        message = await self._session.get(InquiryMessage, before_message_id)
        if (
            message is None
            or message.telegram_chat_id != chat_id
            or message.message_source == InquiryMessageSource.CASHOUT_PANEL
        ):
            raise InquiryValidationError("Invalid before_message_id")
        return message.message_date, message.id

    async def _sender_usernames(
        self,
        messages: list[InquiryMessage],
    ) -> dict[int, str | None]:
        user_ids = {
            message.sent_by_teleledger_user_id
            for message in messages
            if message.sent_by_teleledger_user_id is not None
        }
        if not user_ids:
            return {}
        statement = select(User.id, User.username).where(User.id.in_(user_ids))
        rows = await self._session.execute(statement)
        return {int(row[0]): str(row[1]) for row in rows.all()}

    async def _send_via_telegram(
        self,
        client,
        entity,
        *,
        text: str | None,
        image: UploadFile | None,
    ):
        if image is None:
            return await client.send_message(
                entity,
                message=text or "",
                link_preview=False,
            )

        content = await image.read()
        if not content:
            raise InquiryValidationError("Uploaded image is empty")
        if len(content) > self._settings.inquiry_media_max_bytes:
            raise InquiryValidationError("Uploaded image exceeds the size limit")

        mime_type = image.content_type or mimetypes.guess_type(image.filename or "")[0]
        if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
            raise InquiryValidationError("Only JPEG, PNG, and WEBP images are supported")

        suffix = mimetypes.guess_extension(mime_type) or ".bin"
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as handle:
            handle.write(content)
            temp_path = Path(handle.name)

        try:
            return await client.send_file(
                entity,
                file=str(temp_path),
                caption=text or "",
                force_document=False,
            )
        finally:
            temp_path.unlink(missing_ok=True)

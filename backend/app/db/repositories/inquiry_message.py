from __future__ import annotations

import secrets
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError

from app.db.repositories.base import BaseRepository
from app.models.inquiry_message import (
    InquiryDirection,
    InquiryMediaDownloadStatus,
    InquiryMediaType,
    InquiryMessage,
    InquiryMessageSource,
    InquirySenderAlias,
)

_ALIAS_ADJECTIVES = (
    "Amber",
    "Blue",
    "Bright",
    "Calm",
    "Copper",
    "Crimson",
    "Emerald",
    "Golden",
    "Hidden",
    "Ivory",
    "Misty",
    "Quiet",
    "Ruby",
    "Silver",
    "Soft",
    "Sunny",
    "Velvet",
    "Warm",
)
_ALIAS_NOUNS = (
    "Cedar",
    "Falcon",
    "Fox",
    "Harbor",
    "Lantern",
    "Maple",
    "Meadow",
    "Moon",
    "Owl",
    "Panda",
    "Pine",
    "River",
    "Sparrow",
    "Stone",
    "Summit",
    "Willow",
)
_MAX_ALIAS_ATTEMPTS = len(_ALIAS_ADJECTIVES) * len(_ALIAS_NOUNS) + 100


@dataclass(frozen=True, slots=True)
class InquiryListPage:
    items: list[InquiryMessage]
    has_more: bool
    next_cursor: str | None


class InquiryMessageRepository(BaseRepository[InquiryMessage]):
    """Persistence helpers for cashout-group inquiry chat messages."""

    async def get_by_telegram_identity(
        self,
        *,
        telegram_chat_id: int,
        telegram_message_id: int,
        for_update: bool = False,
    ) -> InquiryMessage | None:
        statement = select(InquiryMessage).where(
            InquiryMessage.telegram_chat_id == telegram_chat_id,
            InquiryMessage.telegram_message_id == telegram_message_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_by_idempotency_key(
        self,
        idempotency_key: str,
    ) -> InquiryMessage | None:
        statement = select(InquiryMessage).where(
            InquiryMessage.idempotency_key == idempotency_key
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def upsert(
        self,
        message: InquiryMessage,
        *,
        preserve_source: bool = True,
        preserve_outbound_metadata: bool = True,
    ) -> tuple[InquiryMessage, bool]:
        """Insert or update one inquiry message by Telegram identity."""
        existing = await self.get_by_telegram_identity(
            telegram_chat_id=message.telegram_chat_id,
            telegram_message_id=message.telegram_message_id,
            for_update=True,
        )
        if existing is None:
            self._session.add(message)
            try:
                await self._session.flush()
            except IntegrityError:
                existing = await self.get_by_telegram_identity(
                    telegram_chat_id=message.telegram_chat_id,
                    telegram_message_id=message.telegram_message_id,
                    for_update=True,
                )
                if existing is None:
                    raise
                self._apply_update(
                    existing,
                    message,
                    preserve_source=preserve_source,
                    preserve_outbound_metadata=preserve_outbound_metadata,
                )
                await self._session.flush()
                return existing, False
            return message, True

        self._apply_update(
            existing,
            message,
            preserve_source=preserve_source,
            preserve_outbound_metadata=preserve_outbound_metadata,
        )
        await self._session.flush()
        return existing, False

    async def list_visible_messages(
        self,
        *,
        telegram_chat_id: int,
        limit: int,
        before_cursor: tuple[datetime, int] | None = None,
    ) -> InquiryListPage:
        """Return visible inquiry messages newest-first for cursor pagination."""
        conditions = [
            InquiryMessage.telegram_chat_id == telegram_chat_id,
            InquiryMessage.message_source != InquiryMessageSource.CASHOUT_PANEL,
        ]
        if before_cursor is not None:
            cursor_date, cursor_id = before_cursor
            conditions.append(
                or_(
                    InquiryMessage.message_date < cursor_date,
                    and_(
                        InquiryMessage.message_date == cursor_date,
                        InquiryMessage.id < cursor_id,
                    ),
                )
            )

        statement = (
            select(InquiryMessage)
            .where(*conditions)
            .order_by(
                InquiryMessage.message_date.desc(),
                InquiryMessage.id.desc(),
            )
            .limit(limit + 1)
        )
        rows = list((await self._session.execute(statement)).scalars().all())
        has_more = len(rows) > limit
        items = rows[:limit]
        next_cursor = None
        if has_more and items:
            oldest = items[-1]
            next_cursor = f"{oldest.message_date.isoformat()}|{oldest.id}"
        return InquiryListPage(items=items, has_more=has_more, next_cursor=next_cursor)

    async def has_visible_grouping_break(
        self,
        *,
        telegram_chat_id: int,
        previous: InquiryMessage,
        current: InquiryMessage,
    ) -> bool:
        """Return True when a visible sender block must break between two messages."""
        if previous.telegram_sender_id != current.telegram_sender_id:
            return True
        if previous.direction != current.direction:
            return True
        if current.message_source == InquiryMessageSource.INQUIRY:
            return True
        if previous.message_source == InquiryMessageSource.INQUIRY:
            return True

        statement = select(InquiryMessage.id).where(
            InquiryMessage.telegram_chat_id == telegram_chat_id,
            InquiryMessage.message_date > previous.message_date,
            InquiryMessage.message_date < current.message_date,
            or_(
                InquiryMessage.message_source == InquiryMessageSource.INQUIRY,
                and_(
                    InquiryMessage.message_source
                    == InquiryMessageSource.TELEGRAM_EXTERNAL,
                    InquiryMessage.telegram_sender_id != previous.telegram_sender_id,
                ),
            ),
        )
        return (await self._session.execute(statement.limit(1))).scalar_one_or_none() is not None

    async def list_pending_media(self, *, limit: int) -> list[InquiryMessage]:
        """Return inquiry rows whose media download still needs work."""
        statement = (
            select(InquiryMessage)
            .where(
                InquiryMessage.media_download_status.in_(
                    (
                        InquiryMediaDownloadStatus.PENDING,
                        InquiryMediaDownloadStatus.FAILED,
                    )
                ),
                InquiryMessage.media_type != InquiryMediaType.NONE,
                InquiryMessage.media_mime_type.is_not(None),
                InquiryMessage.is_deleted.is_(False),
            )
            .order_by(
                InquiryMessage.message_date.desc(),
                InquiryMessage.id.desc(),
            )
            .limit(limit)
        )
        return list((await self._session.execute(statement)).scalars().all())

    async def aliases_for_messages(
        self,
        messages: list[InquiryMessage],
    ) -> dict[int, str]:
        """Return stable aliases for external sender IDs in the supplied rows."""
        sender_ids = {
            int(message.telegram_sender_id)
            for message in messages
            if message.telegram_sender_id is not None
            and message.message_source == InquiryMessageSource.TELEGRAM_EXTERNAL
        }
        aliases: dict[int, str] = {}
        for sender_id in sorted(sender_ids):
            aliases[sender_id] = await self.ensure_sender_alias(sender_id)
        return aliases

    async def ensure_sender_alias(self, telegram_sender_id: int) -> str:
        """Get or create the stable public alias for one external sender."""
        existing = await self._get_sender_alias(telegram_sender_id)
        if existing is not None:
            return existing

        for _attempt in range(_MAX_ALIAS_ATTEMPTS):
            candidate = _random_alias()
            try:
                async with self._session.begin_nested():
                    self._session.add(
                        InquirySenderAlias(
                            telegram_sender_id=telegram_sender_id,
                            alias=candidate,
                        )
                    )
                    await self._session.flush()
                return candidate
            except IntegrityError:
                existing = await self._get_sender_alias(telegram_sender_id)
                if existing is not None:
                    return existing

        raise RuntimeError("Unable to allocate a unique inquiry sender alias")

    async def _get_sender_alias(self, telegram_sender_id: int) -> str | None:
        statement = select(InquirySenderAlias.alias).where(
            InquirySenderAlias.telegram_sender_id == telegram_sender_id
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    @staticmethod
    def _apply_update(
        existing: InquiryMessage,
        incoming: InquiryMessage,
        *,
        preserve_source: bool,
        preserve_outbound_metadata: bool,
    ) -> None:
        if preserve_source:
            incoming.message_source = existing.message_source
        if preserve_outbound_metadata and existing.sent_by_teleledger_user_id is not None:
            incoming.sent_by_teleledger_user_id = existing.sent_by_teleledger_user_id
        if preserve_outbound_metadata and existing.idempotency_key is not None:
            incoming.idempotency_key = existing.idempotency_key

        existing.telegram_sender_id = incoming.telegram_sender_id
        existing.sender_display_name = incoming.sender_display_name
        existing.sender_username = incoming.sender_username
        existing.text = incoming.text
        existing.caption = incoming.caption
        existing.message_date = incoming.message_date
        existing.edited_at = incoming.edited_at
        existing.direction = incoming.direction
        existing.message_source = incoming.message_source
        existing.media_type = incoming.media_type
        existing.media_mime_type = incoming.media_mime_type
        existing.media_filename = incoming.media_filename
        existing.telegram_grouped_id = incoming.telegram_grouped_id
        existing.reply_to_telegram_message_id = incoming.reply_to_telegram_message_id
        existing.forward_from_display_name = incoming.forward_from_display_name
        if incoming.is_deleted:
            existing.is_deleted = True
        if incoming.media_storage_key is not None:
            existing.media_storage_key = incoming.media_storage_key
        if incoming.media_size_bytes is not None:
            existing.media_size_bytes = incoming.media_size_bytes
        if incoming.media_download_status != InquiryMediaDownloadStatus.NOT_APPLICABLE:
            existing.media_download_status = incoming.media_download_status
        if incoming.media_hash is not None:
            existing.media_hash = incoming.media_hash
        if incoming.media_error is not None:
            existing.media_error = incoming.media_error
        elif incoming.media_download_status == InquiryMediaDownloadStatus.READY:
            existing.media_error = None
        if incoming.sent_by_teleledger_user_id is not None:
            existing.sent_by_teleledger_user_id = incoming.sent_by_teleledger_user_id
        if incoming.idempotency_key is not None:
            existing.idempotency_key = incoming.idempotency_key


def _random_alias() -> str:
    adjective = secrets.choice(_ALIAS_ADJECTIVES)
    noun = secrets.choice(_ALIAS_NOUNS)
    alias = f"{adjective} {noun}"
    if secrets.randbelow(12) == 0:
        alias = f"{alias} {secrets.randbelow(90) + 10}"
    return alias

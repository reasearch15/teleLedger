from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.base import BaseRepository
from app.models.telegram_backfill_checkpoint import TelegramBackfillCheckpoint


class TelegramBackfillCheckpointRepository(
    BaseRepository[TelegramBackfillCheckpoint]
):
    """Persistence operations for Telegram backfill checkpoints."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get(
        self,
        telegram_chat_id: int,
        *,
        for_update: bool = False,
    ) -> TelegramBackfillCheckpoint | None:
        """Find the current checkpoint for one Telegram chat."""
        statement = select(TelegramBackfillCheckpoint).where(
            TelegramBackfillCheckpoint.telegram_chat_id == telegram_chat_id
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

    async def upsert(
        self,
        telegram_chat_id: int,
        last_scanned_message_id: int,
    ) -> TelegramBackfillCheckpoint:
        """Create or advance the checkpoint for one Telegram chat."""
        checkpoint = await self.get(telegram_chat_id, for_update=True)
        if checkpoint is None:
            checkpoint = TelegramBackfillCheckpoint(
                telegram_chat_id=telegram_chat_id,
                last_scanned_message_id=last_scanned_message_id,
                updated_at=datetime.now(UTC),
            )
            self._session.add(checkpoint)
        else:
            checkpoint.last_scanned_message_id = last_scanned_message_id
            checkpoint.updated_at = datetime.now(UTC)
        await self._session.flush()
        return checkpoint

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.base import BaseRepository
from app.models.telegram_message import TelegramMessage


class TelegramMessageRepository(BaseRepository[TelegramMessage]):
    """Persistence operations for raw Telegram messages."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def add(self, message: TelegramMessage) -> TelegramMessage:
        """Stage a Telegram message and assign database-generated values."""
        self._session.add(message)
        await self._session.flush()
        return message

    async def flush(self, message: TelegramMessage) -> None:
        """Flush changes to an existing raw Telegram message."""
        await self._session.flush()

    async def add_if_absent(
        self,
        message: TelegramMessage,
    ) -> tuple[TelegramMessage, bool]:
        """Insert once, safely resolving a concurrent unique-key collision."""
        existing = await self.get_by_telegram_identity(
            message.telegram_chat_id,
            message.telegram_message_id,
            for_update=True,
        )
        if existing is not None:
            return existing, False

        try:
            async with self._session.begin_nested():
                self._session.add(message)
                await self._session.flush()
        except IntegrityError:
            existing = await self.get_by_telegram_identity(
                message.telegram_chat_id,
                message.telegram_message_id,
                for_update=True,
            )
            if existing is None:
                raise
            return existing, False

        return message, True

    async def get_by_telegram_identity(
        self,
        telegram_chat_id: int,
        telegram_message_id: int,
        *,
        for_update: bool = False,
    ) -> TelegramMessage | None:
        """Find a message by its stable Telegram chat/message identity."""
        statement = select(TelegramMessage).where(
            TelegramMessage.telegram_chat_id == telegram_chat_id,
            TelegramMessage.telegram_message_id == telegram_message_id,
        )
        if for_update:
            statement = statement.with_for_update()
        result = await self._session.execute(statement)
        return result.scalar_one_or_none()

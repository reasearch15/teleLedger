from __future__ import annotations

from datetime import datetime

from sqlalchemy import delete, select

from app.db.repositories.base import BaseRepository
from app.models.cashout_partial_pending import CashoutPartialPendingInput


class CashoutPartialPendingRepository(BaseRepository[CashoutPartialPendingInput]):
    """Persistence for short-lived Telegram partial-payment input state."""

    async def get_active_for_cashout(
        self,
        cashout_id: int,
        *,
        now: datetime,
        for_update: bool = False,
    ) -> CashoutPartialPendingInput | None:
        statement = select(CashoutPartialPendingInput).where(
            CashoutPartialPendingInput.cashout_id == cashout_id,
            CashoutPartialPendingInput.expires_at > now,
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_active_for_user_in_chat(
        self,
        *,
        telegram_user_id: int,
        telegram_chat_id: int,
        now: datetime,
        for_update: bool = False,
    ) -> CashoutPartialPendingInput | None:
        statement = (
            select(CashoutPartialPendingInput)
            .where(
                CashoutPartialPendingInput.telegram_user_id == telegram_user_id,
                CashoutPartialPendingInput.telegram_chat_id == telegram_chat_id,
                CashoutPartialPendingInput.expires_at > now,
            )
            .order_by(CashoutPartialPendingInput.created_at.desc())
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update()
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def upsert_pending(
        self,
        pending: CashoutPartialPendingInput,
    ) -> CashoutPartialPendingInput:
        await self._session.execute(
            delete(CashoutPartialPendingInput).where(
                CashoutPartialPendingInput.cashout_id == pending.cashout_id
            )
        )
        self._session.add(pending)
        await self._session.flush()
        return pending

    async def delete_for_cashout(self, cashout_id: int) -> None:
        await self._session.execute(
            delete(CashoutPartialPendingInput).where(
                CashoutPartialPendingInput.cashout_id == cashout_id
            )
        )

    async def delete_expired(self, now: datetime) -> int:
        result = await self._session.execute(
            delete(CashoutPartialPendingInput).where(
                CashoutPartialPendingInput.expires_at <= now
            )
        )
        return int(result.rowcount or 0)

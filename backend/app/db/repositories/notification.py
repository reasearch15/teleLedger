from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import func, select

from app.db.repositories.base import BaseRepository
from app.models.notification import PersistentNotification


class PersistentNotificationRepository(BaseRepository[PersistentNotification]):
    """Persistence helpers for durable user notifications."""

    async def add(
        self,
        notification: PersistentNotification,
    ) -> PersistentNotification:
        self._session.add(notification)
        await self._session.flush()
        return notification

    async def list_unread_for_recipient(
        self,
        recipient_user_id: int,
    ) -> list[PersistentNotification]:
        statement = (
            select(PersistentNotification)
            .where(
                PersistentNotification.recipient_user_id == recipient_user_id,
                PersistentNotification.read_at.is_(None),
            )
            .order_by(PersistentNotification.created_at.desc(), PersistentNotification.id.desc())
        )
        return list((await self._session.scalars(statement)).all())

    async def list_for_recipient(
        self,
        recipient_user_id: int,
        *,
        unread_only: bool = False,
        limit: int = 30,
    ) -> list[PersistentNotification]:
        statement = select(PersistentNotification).where(
            PersistentNotification.recipient_user_id == recipient_user_id
        )
        if unread_only:
            statement = statement.where(PersistentNotification.read_at.is_(None))
        statement = statement.order_by(
            PersistentNotification.created_at.desc(),
            PersistentNotification.id.desc(),
        ).limit(limit)
        return list((await self._session.scalars(statement)).all())

    async def unread_count_for_recipient(self, recipient_user_id: int) -> int:
        count = await self._session.scalar(
            select(func.count(PersistentNotification.id)).where(
                PersistentNotification.recipient_user_id == recipient_user_id,
                PersistentNotification.read_at.is_(None),
            )
        )
        return int(count or 0)

    async def mark_read_for_recipient(
        self,
        notification_id: int,
        recipient_user_id: int,
    ) -> PersistentNotification | None:
        statement = select(PersistentNotification).where(
            PersistentNotification.id == notification_id,
            PersistentNotification.recipient_user_id == recipient_user_id,
        ).with_for_update()
        notification = (await self._session.execute(statement)).scalar_one_or_none()
        if notification is not None and notification.read_at is None:
            notification.read_at = datetime.now(UTC)
            await self._session.flush()
        return notification

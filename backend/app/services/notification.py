from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.notification import PersistentNotificationRepository
from app.models.notification import NotificationType, PersistentNotification
from app.models.user import User
from app.services.base import ApplicationService
from app.websocket.events import LiveEventType, event_broker


class NotificationNotFoundError(Exception):
    """Raised when a notification is not visible to the recipient."""


class NotificationService(ApplicationService):
    """Persistent Atlas notification data layer."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = PersistentNotificationRepository(session)

    async def create(
        self,
        *,
        recipient_user_id: int,
        notification_type: NotificationType,
        related_entity_type: str,
        related_entity_id: int,
        title: str,
        body: str | None,
        coadmin_id: int | None = None,
        payload: dict[str, object] | None = None,
    ) -> PersistentNotification:
        notification = await self._repository.add(
            PersistentNotification(
                recipient_user_id=recipient_user_id,
                coadmin_id=coadmin_id,
                type=notification_type,
                related_entity_type=related_entity_type,
                related_entity_id=related_entity_id,
                title=title,
                body=body,
                payload=payload,
            )
        )
        await event_broker.publish(
            LiveEventType.NOTIFICATION_CREATED,
            notification_id=notification.id,
            user_id=recipient_user_id,
        )
        return notification

    async def list_unread(self, actor: User) -> list[PersistentNotification]:
        return await self._repository.list_unread_for_recipient(actor.id)

    async def list_for_actor(
        self,
        actor: User,
        *,
        unread_only: bool = False,
        limit: int = 30,
    ) -> list[PersistentNotification]:
        return await self._repository.list_for_recipient(
            actor.id,
            unread_only=unread_only,
            limit=limit,
        )

    async def unread_count(self, actor: User) -> int:
        return await self._repository.unread_count_for_recipient(actor.id)

    async def mark_read(self, notification_id: int, actor: User) -> PersistentNotification:
        notification = await self._repository.mark_read_for_recipient(
            notification_id,
            actor.id,
        )
        if notification is None:
            raise NotificationNotFoundError("Notification was not found.")
        await event_broker.publish(
            LiveEventType.NOTIFICATION_READ,
            notification_id=notification.id,
            user_id=actor.id,
        )
        return notification

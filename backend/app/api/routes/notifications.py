from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict

from app.api.dependencies import CurrentUser, DatabaseSession
from app.models.notification import NotificationType, PersistentNotification
from app.services.notification import NotificationNotFoundError, NotificationService

router = APIRouter(prefix="/api/notifications", tags=["notifications"])


class NotificationResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    recipient_user_id: int
    coadmin_id: int | None
    type: NotificationType
    related_entity_type: str
    related_entity_id: int
    title: str
    body: str | None
    payload: dict[str, Any] | None
    created_at: datetime
    read_at: datetime | None
    navigation_href: str | None = None


class NotificationListResponse(BaseModel):
    items: list[NotificationResponse]
    unread_count: int


class NotificationCountResponse(BaseModel):
    unread_count: int


@router.get("", response_model=NotificationListResponse)
async def list_notifications(
    session: DatabaseSession,
    current_user: CurrentUser,
    unread_only: bool = False,
    limit: Annotated[int, Query(ge=1, le=100)] = 30,
) -> NotificationListResponse:
    service = NotificationService(session)
    notifications = await service.list_for_actor(
        current_user,
        unread_only=unread_only,
        limit=limit,
    )
    return NotificationListResponse(
        items=[_serialize_notification(notification) for notification in notifications],
        unread_count=await service.unread_count(current_user),
    )


@router.get("/unread-count", response_model=NotificationCountResponse)
async def unread_notification_count(
    session: DatabaseSession,
    current_user: CurrentUser,
) -> NotificationCountResponse:
    return NotificationCountResponse(
        unread_count=await NotificationService(session).unread_count(current_user)
    )


@router.post("/{notification_id}/read", response_model=NotificationResponse)
async def mark_notification_read(
    notification_id: int,
    session: DatabaseSession,
    current_user: CurrentUser,
) -> NotificationResponse:
    try:
        notification = await NotificationService(session).mark_read(
            notification_id,
            current_user,
        )
        await session.commit()
    except NotificationNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    return _serialize_notification(notification)


def _serialize_notification(
    notification: PersistentNotification,
) -> NotificationResponse:
    return NotificationResponse.model_validate(notification).model_copy(
        update={"navigation_href": _navigation_href(notification)}
    )


def _navigation_href(notification: PersistentNotification) -> str | None:
    if notification.related_entity_type == "venmo_confirmation_request":
        return f"/venmo-confirmations/{notification.related_entity_id}"
    if notification.related_entity_type == "cashout_request":
        return "/cashout"
    return None

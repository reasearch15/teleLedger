from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.repositories.media_asset import MediaAssetRepository
from app.db.repositories.venmo_confirmation import VenmoConfirmationRepository
from app.models.media_asset import MediaAsset
from app.models.notification import NotificationType
from app.models.user import User, UserRole
from app.models.venmo_confirmation import (
    VenmoConfirmationAttempt,
    VenmoConfirmationAttemptStatus,
    VenmoConfirmationEvent,
    VenmoConfirmationEventType,
    VenmoConfirmationInquiry,
    VenmoConfirmationInquiryStatus,
    VenmoConfirmationRequest,
    VenmoConfirmationStatus,
)
from app.services.base import ApplicationService
from app.services.notification import NotificationService

logger = get_logger(__name__)


class VenmoConfirmationAuthorizationError(Exception):
    """Raised when an actor cannot access a Venmo confirmation workflow."""


class VenmoConfirmationNotFoundError(Exception):
    """Raised when a scoped Venmo confirmation row is not found."""


class VenmoConfirmationStateConflictError(Exception):
    """Raised when a Venmo confirmation transition is invalid."""


class VenmoConfirmationService(ApplicationService):
    """Data-layer operations for Venmo confirmation workflows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = VenmoConfirmationRepository(session)
        self._media_repository = MediaAssetRepository(session)

    async def create_request(
        self,
        *,
        actor: User,
        screenshot_media_asset_id: int,
        payment_note: str | None = None,
        metadata: dict[str, object] | None = None,
    ) -> VenmoConfirmationRequest:
        self._require_staff(actor)
        if actor.coadmin_id is None:
            raise VenmoConfirmationAuthorizationError(
                "Staff must be assigned to a coadmin before requesting confirmation."
            )
        media = await self._media_repository.get_for_coadmin(
            screenshot_media_asset_id,
            actor.coadmin_id,
        )
        if media is None:
            raise VenmoConfirmationNotFoundError("Screenshot media was not found.")
        request = await self._repository.add_request(
            VenmoConfirmationRequest(
                coadmin_id=actor.coadmin_id,
                requested_by_staff_id=actor.id,
                screenshot_media_asset_id=media.id,
                payment_note=payment_note,
                metadata_json=metadata,
            )
        )
        await self._record_event(
            request_id=request.id,
            event_type=VenmoConfirmationEventType.REQUEST_CREATED,
            actor=actor,
            payload={"screenshot_media_asset_id": media.id},
        )
        return request

    async def get_request_for_coadmin(
        self,
        request_id: int,
        coadmin_id: int,
    ) -> VenmoConfirmationRequest:
        request = await self._repository.get_request_for_coadmin(request_id, coadmin_id)
        if request is None:
            raise VenmoConfirmationNotFoundError("Venmo confirmation was not found.")
        return request

    async def list_requests_for_actor(
        self,
        *,
        actor: User,
        limit: int = 50,
        offset: int = 0,
    ) -> list[VenmoConfirmationRequest]:
        if actor.role == UserRole.ADMIN:
            return await self._repository.list_requests(limit=limit, offset=offset)
        coadmin_id = self._actor_coadmin_id(actor)
        return await self._repository.list_requests_for_coadmin(
            coadmin_id,
            limit=limit,
            offset=offset,
        )

    async def get_request_for_actor(
        self,
        request_id: int,
        *,
        actor: User,
    ) -> VenmoConfirmationRequest:
        if actor.role == UserRole.ADMIN:
            request = await self._repository.get_by_id(request_id)
        else:
            request = await self._repository.get_request_for_coadmin(
                request_id,
                self._actor_coadmin_id(actor),
            )
        if request is None:
            raise VenmoConfirmationNotFoundError("Venmo confirmation was not found.")
        return request

    async def get_detail_for_actor(
        self,
        request_id: int,
        *,
        actor: User,
    ) -> tuple[
        VenmoConfirmationRequest,
        MediaAsset,
        list[VenmoConfirmationAttempt],
        list[VenmoConfirmationInquiry],
        list[VenmoConfirmationEvent],
    ]:
        request = await self.get_request_for_actor(request_id, actor=actor)
        media = await self._media_repository.get_by_id(request.screenshot_media_asset_id)
        if media is None:
            raise VenmoConfirmationNotFoundError("Screenshot media was not found.")
        if actor.role != UserRole.ADMIN and media.coadmin_id != self._actor_coadmin_id(actor):
            raise VenmoConfirmationNotFoundError("Screenshot media was not found.")
        return (
            request,
            media,
            await self._repository.list_attempts(request.id),
            await self._repository.list_inquiries(request.id),
            await self._repository.list_events(request.id),
        )

    async def get_media_for_actor(self, media_id: int, *, actor: User) -> MediaAsset:
        if actor.role == UserRole.ADMIN:
            media = await self._media_repository.get_by_id(media_id)
        else:
            media = await self._media_repository.get_for_coadmin(
                media_id,
                self._actor_coadmin_id(actor),
            )
        if media is None:
            raise VenmoConfirmationNotFoundError("Media was not found.")
        return media

    async def create_attempt(
        self,
        *,
        request_id: int,
        coadmin_id: int,
    ) -> VenmoConfirmationAttempt:
        request = await self._repository.get_request_for_coadmin(
            request_id,
            coadmin_id,
            for_update=True,
        )
        if request is None:
            raise VenmoConfirmationNotFoundError("Venmo confirmation was not found.")
        attempt = await self._repository.add_attempt(
            VenmoConfirmationAttempt(
                request_id=request.id,
                attempt_number=await self._repository.next_attempt_number(request.id),
            )
        )
        await self._record_event(
            request_id=request.id,
            attempt_id=attempt.id,
            event_type=VenmoConfirmationEventType.ATTEMPT_CREATED,
            payload={"attempt_number": attempt.attempt_number},
        )
        return attempt

    async def mark_attempt_not_received(
        self,
        *,
        attempt_id: int,
        coadmin_id: int,
    ) -> VenmoConfirmationInquiry:
        attempt = await self._repository.get_attempt_for_coadmin(
            attempt_id,
            coadmin_id,
            for_update=True,
        )
        if attempt is None:
            raise VenmoConfirmationNotFoundError("Venmo confirmation attempt was not found.")
        if attempt.status in (
            VenmoConfirmationAttemptStatus.CONFIRMED,
            VenmoConfirmationAttemptStatus.NOT_RECEIVED,
        ):
            raise VenmoConfirmationStateConflictError("Attempt was already resolved.")
        now = datetime.now(UTC)
        attempt.status = VenmoConfirmationAttemptStatus.NOT_RECEIVED
        attempt.resolved_at = now
        request = await self._repository.get_request_for_coadmin(
            attempt.request_id,
            coadmin_id,
            for_update=True,
        )
        assert request is not None
        request.status = VenmoConfirmationStatus.NOT_RECEIVED
        inquiry = await self._repository.add_inquiry(
            VenmoConfirmationInquiry(
                request_id=request.id,
                source_attempt_id=attempt.id,
            )
        )
        await self._record_event(
            request_id=request.id,
            attempt_id=attempt.id,
            event_type=VenmoConfirmationEventType.NOT_RECEIVED,
        )
        await self._record_event(
            request_id=request.id,
            attempt_id=attempt.id,
            inquiry_id=inquiry.id,
            event_type=VenmoConfirmationEventType.INQUIRY_CREATED,
        )
        return inquiry

    async def dismiss_inquiry(
        self,
        *,
        inquiry_id: int,
        coadmin_id: int,
        actor: User,
    ) -> VenmoConfirmationInquiry:
        self._require_coadmin_actor(actor, coadmin_id)
        inquiry = await self._repository.get_inquiry_for_coadmin(
            inquiry_id,
            coadmin_id,
            for_update=True,
        )
        if inquiry is None:
            raise VenmoConfirmationNotFoundError("Venmo confirmation inquiry was not found.")
        if inquiry.status != VenmoConfirmationInquiryStatus.OPEN:
            raise VenmoConfirmationStateConflictError("Inquiry is already closed.")
        inquiry.status = VenmoConfirmationInquiryStatus.DISMISSED
        inquiry.dismissed_at = datetime.now(UTC)
        inquiry.dismissed_by_staff_id = actor.id
        await self._record_event(
            request_id=inquiry.request_id,
            inquiry_id=inquiry.id,
            event_type=VenmoConfirmationEventType.INQUIRY_DISMISSED,
            actor=actor,
        )
        return inquiry

    async def mark_confirmed(
        self,
        *,
        attempt_id: int,
        coadmin_id: int,
        telegram_user_id: int | None = None,
        telegram_username: str | None = None,
        display_name: str | None = None,
    ) -> VenmoConfirmationRequest:
        attempt = await self._repository.get_attempt_for_coadmin(
            attempt_id,
            coadmin_id,
            for_update=True,
        )
        if attempt is None:
            raise VenmoConfirmationNotFoundError("Venmo confirmation attempt was not found.")
        if attempt.status in (
            VenmoConfirmationAttemptStatus.CONFIRMED,
            VenmoConfirmationAttemptStatus.NOT_RECEIVED,
        ):
            raise VenmoConfirmationStateConflictError("Attempt was already resolved.")
        request = await self._repository.get_request_for_coadmin(
            attempt.request_id,
            coadmin_id,
            for_update=True,
        )
        assert request is not None
        now = datetime.now(UTC)
        attempt.status = VenmoConfirmationAttemptStatus.CONFIRMED
        attempt.resolved_at = now
        request.status = VenmoConfirmationStatus.CONFIRMED
        request.confirmed_at = now
        request.confirmed_by_telegram_user_id = telegram_user_id
        request.confirmed_by_telegram_username = telegram_username
        request.confirmed_by_display_name = display_name
        await self._record_event(
            request_id=request.id,
            attempt_id=attempt.id,
            event_type=VenmoConfirmationEventType.CONFIRMED,
            actor_source="telegram",
            actor_identifier=str(telegram_user_id) if telegram_user_id is not None else None,
            payload={
                "telegram_user_id": telegram_user_id,
                "telegram_username": telegram_username,
                "display_name": display_name,
            },
        )
        if request.requested_by_staff_id is not None:
            try:
                await NotificationService(self._session).create(
                    recipient_user_id=request.requested_by_staff_id,
                    coadmin_id=request.coadmin_id,
                    notification_type=NotificationType.VENMO_CONFIRMATION_CONFIRMED,
                    related_entity_type="venmo_confirmation_request",
                    related_entity_id=request.id,
                    title="Venmo payment confirmed",
                    body="Payment was confirmed and can proceed.",
                    payload={"request_id": request.id, "attempt_id": attempt.id},
                )
            except Exception:
                logger.exception(
                    "venmo_confirmation_notification_failed",
                    extra={
                        "venmo_confirmation_request_id": request.id,
                        "coadmin_id": request.coadmin_id,
                    },
                )
        return request

    async def create_media_asset(
        self,
        *,
        coadmin_id: int,
        storage_key: str,
        original_filename: str | None,
        mime_type: str,
        size_bytes: int,
        checksum_sha256: str,
        actor: User,
    ) -> MediaAsset:
        self._require_coadmin_actor(actor, coadmin_id)
        return await self._media_repository.add(
            MediaAsset(
                coadmin_id=coadmin_id,
                storage_key=storage_key,
                original_filename=original_filename,
                mime_type=mime_type,
                size_bytes=size_bytes,
                checksum_sha256=checksum_sha256,
                created_by_user_id=actor.id,
            )
        )

    async def replace_payment_screenshot(
        self,
        *,
        request_id: int,
        actor: User,
        storage_key: str,
        original_filename: str | None,
        mime_type: str,
        size_bytes: int,
        checksum_sha256: str,
    ) -> MediaAsset:
        request = await self.get_request_for_actor(request_id, actor=actor)
        self._require_coadmin_actor(actor, request.coadmin_id)
        media = await self.create_media_asset(
            coadmin_id=request.coadmin_id,
            storage_key=storage_key,
            original_filename=original_filename,
            mime_type=mime_type,
            size_bytes=size_bytes,
            checksum_sha256=checksum_sha256,
            actor=actor,
        )
        previous_media_asset_id = request.screenshot_media_asset_id
        request.screenshot_media_asset_id = media.id
        await self._record_event(
            request_id=request.id,
            event_type=VenmoConfirmationEventType.PAYMENT_SCREENSHOT_UPLOADED,
            actor=actor,
            payload={
                "media_asset_id": media.id,
                "previous_media_asset_id": previous_media_asset_id,
                "mime_type": media.mime_type,
                "size_bytes": media.size_bytes,
            },
        )
        return media

    async def _record_event(
        self,
        *,
        request_id: int,
        event_type: VenmoConfirmationEventType,
        attempt_id: int | None = None,
        inquiry_id: int | None = None,
        actor: User | None = None,
        actor_source: str | None = None,
        actor_identifier: str | None = None,
        payload: dict[str, object] | None = None,
    ) -> VenmoConfirmationEvent:
        return await self._repository.add_event(
            VenmoConfirmationEvent(
                request_id=request_id,
                attempt_id=attempt_id,
                inquiry_id=inquiry_id,
                event_type=event_type,
                actor_user_id=actor.id if actor is not None else None,
                actor_source=actor_source or ("atlas" if actor is not None else "system"),
                actor_identifier=actor_identifier or (str(actor.id) if actor is not None else None),
                payload=payload,
            )
        )

    @staticmethod
    def _require_staff(actor: User) -> None:
        if actor.role != UserRole.STAFF:
            raise VenmoConfirmationAuthorizationError("Staff access is required.")

    @staticmethod
    def _actor_coadmin_id(actor: User) -> int:
        if actor.role == UserRole.COADMIN:
            return actor.id
        if actor.role == UserRole.STAFF and actor.coadmin_id is not None:
            return actor.coadmin_id
        raise VenmoConfirmationAuthorizationError("Venmo confirmation is not accessible.")

    @staticmethod
    def _require_coadmin_actor(actor: User, coadmin_id: int) -> None:
        if actor.role == UserRole.ADMIN:
            return
        if actor.role == UserRole.COADMIN and actor.id == coadmin_id:
            return
        if actor.role == UserRole.STAFF and actor.coadmin_id == coadmin_id:
            return
        raise VenmoConfirmationAuthorizationError("Venmo confirmation is not accessible.")

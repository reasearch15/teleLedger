from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
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
from app.telegram.cashout_bot.api import TelegramBotApiError
from app.telegram.peer_ids import chat_ids_equivalent, normalize_telegram_chat_id
from app.telegram.venmo_confirmation import (
    VenmoConfirmationCallbackAction,
    decode_venmo_confirmation_callback,
    format_venmo_confirmation_confirmed_caption,
    format_venmo_confirmation_not_received_caption,
)

logger = get_logger(__name__)


class VenmoConfirmationAuthorizationError(Exception):
    """Raised when an actor cannot access a Venmo confirmation workflow."""


class VenmoConfirmationNotFoundError(Exception):
    """Raised when a scoped Venmo confirmation row is not found."""


class VenmoConfirmationStateConflictError(Exception):
    """Raised when a Venmo confirmation transition is invalid."""


@dataclass(frozen=True, slots=True)
class VenmoConfirmationTelegramActionResult:
    status: str
    request_id: int | None = None
    attempt_id: int | None = None


@dataclass(frozen=True, slots=True)
class VenmoConfirmationCursor:
    created_at: datetime
    row_id: int


@dataclass(frozen=True, slots=True)
class VenmoConfirmationRequestPage:
    items: list[VenmoConfirmationRequest]
    has_more: bool
    next_cursor: str | None


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
        coadmin_id = self._actor_coadmin_id(actor)
        media = await self._media_repository.get_for_coadmin(
            screenshot_media_asset_id,
            coadmin_id,
        )
        if media is None:
            raise VenmoConfirmationNotFoundError("Screenshot media was not found.")
        request = await self._repository.add_request(
            VenmoConfirmationRequest(
                coadmin_id=coadmin_id,
                requested_by_staff_id=actor.id if actor.role == UserRole.STAFF else None,
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

    async def mark_attempt_posted(
        self,
        *,
        attempt_id: int,
        coadmin_id: int,
        telegram_chat_id: int,
        telegram_message_id: int,
        event_type: VenmoConfirmationEventType = VenmoConfirmationEventType.ATTEMPT_POSTED,
    ) -> VenmoConfirmationAttempt:
        attempt = await self._repository.get_attempt_for_coadmin(
            attempt_id,
            coadmin_id,
            for_update=True,
        )
        if attempt is None:
            raise VenmoConfirmationNotFoundError("Venmo confirmation attempt was not found.")
        attempt.telegram_chat_id = normalize_telegram_chat_id(telegram_chat_id)
        attempt.telegram_message_id = telegram_message_id
        attempt.status = VenmoConfirmationAttemptStatus.POSTED
        attempt.posted_at = datetime.now(UTC)
        attempt.last_error = None
        attempt.next_retry_at = None
        attempt.delivery_lease_until = None
        await self._record_event(
            request_id=attempt.request_id,
            attempt_id=attempt.id,
            event_type=event_type,
            payload={
                "telegram_chat_id": attempt.telegram_chat_id,
                "telegram_message_id": attempt.telegram_message_id,
            },
        )
        return attempt

    async def begin_attempt_delivery(
        self,
        *,
        attempt_id: int,
        coadmin_id: int,
        lease_seconds: int,
    ) -> VenmoConfirmationAttempt:
        attempt = await self._repository.get_attempt_for_coadmin(
            attempt_id,
            coadmin_id,
            for_update=True,
        )
        if attempt is None:
            raise VenmoConfirmationNotFoundError("Venmo confirmation attempt was not found.")
        if attempt.telegram_message_id is not None:
            return attempt
        if attempt.status != VenmoConfirmationAttemptStatus.PENDING:
            raise VenmoConfirmationStateConflictError(
                "Venmo confirmation attempt is not pending delivery."
            )
        attempt.delivery_attempts += 1
        attempt.delivery_lease_until = datetime.now(UTC) + timedelta(seconds=lease_seconds)
        attempt.next_retry_at = None
        await self._session.flush()
        return attempt

    async def record_attempt_retry(
        self,
        *,
        attempt_id: int,
        coadmin_id: int,
        error: str,
        failure_class: str,
        retry_number: int,
        delay_seconds: float,
        next_retry_at: datetime,
        telegram_status_code: int | None = None,
    ) -> VenmoConfirmationAttempt:
        attempt = await self._repository.get_attempt_for_coadmin(
            attempt_id,
            coadmin_id,
            for_update=True,
        )
        if attempt is None:
            raise VenmoConfirmationNotFoundError("Venmo confirmation attempt was not found.")
        if attempt.telegram_message_id is not None:
            return attempt
        attempt.status = VenmoConfirmationAttemptStatus.PENDING
        attempt.last_error = error[:2000]
        attempt.next_retry_at = next_retry_at
        attempt.delivery_lease_until = None
        await self._record_event(
            request_id=attempt.request_id,
            attempt_id=attempt.id,
            event_type=VenmoConfirmationEventType.FAILURE,
            payload={
                "error": attempt.last_error,
                "failure_class": failure_class,
                "retry_number": retry_number,
                "retryable": True,
                "delay_seconds": delay_seconds,
                "next_retry_at": next_retry_at.isoformat(),
                "telegram_status_code": telegram_status_code,
                "terminal": False,
            },
        )
        return attempt

    async def mark_attempt_failed(
        self,
        *,
        attempt_id: int,
        coadmin_id: int,
        error: str,
    ) -> VenmoConfirmationAttempt:
        attempt = await self._repository.get_attempt_for_coadmin(
            attempt_id,
            coadmin_id,
            for_update=True,
        )
        if attempt is None:
            raise VenmoConfirmationNotFoundError("Venmo confirmation attempt was not found.")
        attempt.status = VenmoConfirmationAttemptStatus.FAILED_TO_SEND
        attempt.last_error = error[:2000]
        attempt.next_retry_at = None
        attempt.delivery_lease_until = None
        await self._record_event(
            request_id=attempt.request_id,
            attempt_id=attempt.id,
            event_type=VenmoConfirmationEventType.FAILURE,
            payload={"error": attempt.last_error, "terminal": True},
        )
        return attempt

    async def link_posted_attempt_from_message(
        self,
        *,
        request_id: int,
        attempt_number: int,
        telegram_chat_id: int,
        telegram_message_id: int,
    ) -> VenmoConfirmationAttempt | None:
        request = await self._repository.get_by_id(request_id)
        if request is None:
            return None
        attempt = await self._repository.get_attempt_by_request_number(
            request_id,
            attempt_number,
            for_update=True,
        )
        if attempt is None:
            return None
        if attempt.telegram_message_id is not None:
            return attempt
        return await self.mark_attempt_posted(
            attempt_id=attempt.id,
            coadmin_id=request.coadmin_id,
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=telegram_message_id,
        )

    async def has_active_delivery_for_request(
        self,
        *,
        request_id: int,
        coadmin_id: int,
    ) -> bool:
        latest = await self._repository.latest_attempt_for_request(request_id)
        if latest is None:
            return False
        request = await self._repository.get_request_for_coadmin(request_id, coadmin_id)
        if request is None:
            raise VenmoConfirmationNotFoundError("Venmo confirmation was not found.")
        if latest.telegram_message_id is not None:
            return False
        if latest.status != VenmoConfirmationAttemptStatus.PENDING:
            return False
        return latest.delivery_lease_until is not None or latest.next_retry_at is not None

    async def handle_telegram_callback(
        self,
        *,
        query_id: str,
        callback_data: str,
        telegram_chat_id: int,
        telegram_user_id: int,
        telegram_username: str | None,
        message_id: int,
        gateway: object,
    ) -> VenmoConfirmationTelegramActionResult:
        decoded = decode_venmo_confirmation_callback(callback_data)
        if decoded is None:
            return VenmoConfirmationTelegramActionResult(status="not_venmo_confirmation")

        attempt_id, action = decoded
        attempt = await self._repository.get_attempt_by_id(attempt_id, for_update=True)
        if attempt is None:
            await _answer_gateway_callback(
                gateway,
                query_id=query_id,
                text="Confirmation request was not found.",
                alert=True,
            )
            return VenmoConfirmationTelegramActionResult(
                status="not_found",
                attempt_id=attempt_id,
            )
        request = await self._repository.get_by_id(attempt.request_id)
        if request is None:
            await _answer_gateway_callback(
                gateway,
                query_id=query_id,
                text="Confirmation request was not found.",
                alert=True,
            )
            return VenmoConfirmationTelegramActionResult(
                status="not_found",
                attempt_id=attempt_id,
            )
        expected_chat_id = normalize_telegram_chat_id(get_settings().shared_telegram_supergroup_id)
        normalized_chat_id = normalize_telegram_chat_id(telegram_chat_id)
        if expected_chat_id is None or not chat_ids_equivalent(
            normalized_chat_id,
            expected_chat_id,
        ):
            await _answer_gateway_callback(
                gateway,
                query_id=query_id,
                text="This confirmation belongs to a different group.",
                alert=True,
            )
            return VenmoConfirmationTelegramActionResult(
                status="wrong_group",
                request_id=request.id,
                attempt_id=attempt.id,
            )
        if attempt.telegram_chat_id is not None and not chat_ids_equivalent(
            attempt.telegram_chat_id,
            normalized_chat_id,
        ):
            await _answer_gateway_callback(
                gateway,
                query_id=query_id,
                text="This confirmation belongs to a different message.",
                alert=True,
            )
            return VenmoConfirmationTelegramActionResult(
                status="wrong_message",
                request_id=request.id,
                attempt_id=attempt.id,
            )
        if attempt.telegram_message_id is not None and attempt.telegram_message_id != message_id:
            await _answer_gateway_callback(
                gateway,
                query_id=query_id,
                text="This confirmation belongs to a different message.",
                alert=True,
            )
            return VenmoConfirmationTelegramActionResult(
                status="wrong_message",
                request_id=request.id,
                attempt_id=attempt.id,
            )

        display_name = telegram_username or str(telegram_user_id)
        try:
            if action == VenmoConfirmationCallbackAction.CONFIRM:
                request = await self.mark_confirmed(
                    attempt_id=attempt.id,
                    coadmin_id=request.coadmin_id,
                    telegram_user_id=telegram_user_id,
                    telegram_username=telegram_username,
                    display_name=display_name,
                )
                await self.sync_telegram_terminal_message(
                    request=request,
                    attempt=attempt,
                    gateway=gateway,
                    fallback_display_name=display_name,
                )
                await _answer_gateway_callback(
                    gateway,
                    query_id=query_id,
                    text="Confirmation marked confirmed.",
                )
                return VenmoConfirmationTelegramActionResult(
                    status="confirmed",
                    request_id=request.id,
                    attempt_id=attempt.id,
                )

            await self.mark_attempt_not_received(
                attempt_id=attempt.id,
                coadmin_id=request.coadmin_id,
            )
            request = await self._repository.get_by_id(request.id)
            assert request is not None
            await self.sync_telegram_terminal_message(
                request=request,
                attempt=attempt,
                gateway=gateway,
                fallback_display_name=display_name,
            )
            await _answer_gateway_callback(
                gateway,
                query_id=query_id,
                text="Confirmation marked not received.",
            )
            return VenmoConfirmationTelegramActionResult(
                status="not_received",
                request_id=request.id,
                attempt_id=attempt.id,
            )
        except VenmoConfirmationStateConflictError:
            request = await self._repository.get_by_id(request.id)
            if request is not None:
                await self.sync_telegram_terminal_message(
                    request=request,
                    attempt=attempt,
                    gateway=gateway,
                    fallback_display_name=display_name,
                )
            message = (
                "Already confirmed."
                if request is not None and request.status == VenmoConfirmationStatus.CONFIRMED
                else "Confirmation was already resolved."
            )
            await _answer_gateway_callback(
                gateway,
                query_id=query_id,
                text=message,
                alert=True,
            )
            return VenmoConfirmationTelegramActionResult(
                status="already_resolved",
                request_id=request.id,
                attempt_id=attempt.id,
            )

    async def sync_telegram_terminal_message(
        self,
        *,
        request: VenmoConfirmationRequest,
        attempt: VenmoConfirmationAttempt | None,
        gateway: object,
        fallback_display_name: str | None = None,
    ) -> str:
        if request.status not in (
            VenmoConfirmationStatus.CONFIRMED,
            VenmoConfirmationStatus.NOT_RECEIVED,
        ):
            return "not_terminal"
        if attempt is None:
            attempt = await self._repository.latest_attempt_for_request(request.id)
        if (
            attempt is None
            or attempt.telegram_chat_id is None
            or attempt.telegram_message_id is None
        ):
            return "no_linked_message"
        if request.status == VenmoConfirmationStatus.CONFIRMED:
            caption = format_venmo_confirmation_confirmed_caption(
                request_id=request.id,
                confirmed_by=request.confirmed_by_display_name or fallback_display_name,
                confirmed_at=request.confirmed_at,
            )
        else:
            caption = format_venmo_confirmation_not_received_caption(request_id=request.id)
        edit = getattr(gateway, "edit_message_caption", None)
        if edit is None:
            return "no_gateway_caption_edit"
        try:
            await edit(
                chat_id=attempt.telegram_chat_id,
                message_id=attempt.telegram_message_id,
                caption=caption,
                buttons=None,
            )
        except TelegramBotApiError as error:
            if _is_telegram_message_not_modified(error):
                if attempt.last_error and attempt.last_error.startswith("terminal_sync_failed:"):
                    attempt.last_error = None
                    await self._session.flush()
                logger.info(
                    "venmo_confirmation_terminal_sync_already_synced",
                    extra={
                        "venmo_confirmation_request_id": request.id,
                        "venmo_confirmation_attempt_id": attempt.id,
                        "telegram_chat_id": attempt.telegram_chat_id,
                        "telegram_message_id": attempt.telegram_message_id,
                    },
                )
                return "already_synced"
            attempt.last_error = f"terminal_sync_failed: {error}"[:2000]
            await self._session.flush()
            logger.exception(
                "venmo_confirmation_terminal_sync_failed",
                extra={
                    "venmo_confirmation_request_id": request.id,
                    "venmo_confirmation_attempt_id": attempt.id,
                    "telegram_chat_id": attempt.telegram_chat_id,
                    "telegram_message_id": attempt.telegram_message_id,
                },
            )
            return "failed"
        except Exception as error:
            attempt.last_error = f"terminal_sync_failed: {error}"[:2000]
            await self._session.flush()
            logger.exception(
                "venmo_confirmation_terminal_sync_failed",
                extra={
                    "venmo_confirmation_request_id": request.id,
                    "venmo_confirmation_attempt_id": attempt.id,
                    "telegram_chat_id": attempt.telegram_chat_id,
                    "telegram_message_id": attempt.telegram_message_id,
                },
            )
            return "failed"
        if attempt.last_error and attempt.last_error.startswith("terminal_sync_failed:"):
            attempt.last_error = None
            await self._session.flush()
        logger.info(
            "venmo_confirmation_terminal_sync_succeeded",
            extra={
                "venmo_confirmation_request_id": request.id,
                "venmo_confirmation_attempt_id": attempt.id,
                "telegram_chat_id": attempt.telegram_chat_id,
                "telegram_message_id": attempt.telegram_message_id,
            },
        )
        return "edited_terminal"

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
        cursor: str | None = None,
    ) -> VenmoConfirmationRequestPage:
        parsed_cursor = self._parse_request_cursor(cursor)
        query_limit = limit + 1
        if actor.role == UserRole.ADMIN:
            requests = await self._repository.list_requests(
                limit=query_limit,
                cursor_created_at=parsed_cursor.created_at if parsed_cursor else None,
                cursor_id=parsed_cursor.row_id if parsed_cursor else None,
            )
        else:
            coadmin_id = self._actor_coadmin_id(actor)
            requests = await self._repository.list_requests_for_coadmin(
                coadmin_id,
                limit=query_limit,
                cursor_created_at=parsed_cursor.created_at if parsed_cursor else None,
                cursor_id=parsed_cursor.row_id if parsed_cursor else None,
            )
        has_more = len(requests) > limit
        items = requests[:limit]
        next_cursor = None
        if has_more and items:
            last = items[-1]
            next_cursor = self._next_request_cursor(last.created_at, last.id)
        return VenmoConfirmationRequestPage(
            items=items,
            has_more=has_more,
            next_cursor=next_cursor,
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

    @staticmethod
    def _parse_request_cursor(cursor: str | None) -> VenmoConfirmationCursor | None:
        if not cursor:
            return None
        try:
            raw_created_at, raw_row_id = cursor.split("|", 1)
            created_at = datetime.fromisoformat(raw_created_at)
            return VenmoConfirmationCursor(created_at=created_at, row_id=int(raw_row_id))
        except (TypeError, ValueError) as error:
            raise VenmoConfirmationStateConflictError("Invalid cursor.") from error

    @staticmethod
    def _next_request_cursor(created_at: datetime, row_id: int) -> str:
        return f"{created_at.isoformat()}|{row_id}"


async def _answer_gateway_callback(
    gateway: object,
    *,
    query_id: str,
    text: str,
    alert: bool = False,
) -> None:
    try:
        await gateway.answer_callback_query(query_id=query_id, text=text, alert=alert)
    except Exception:
        logger.warning("venmo_confirmation_callback_answer_failed", exc_info=True)


def _is_telegram_message_not_modified(error: TelegramBotApiError) -> bool:
    return (
        error.status_code == 400
        and "message is not modified" in str(error).casefold()
    )


async def _edit_gateway_caption(
    gateway: object,
    *,
    chat_id: int,
    message_id: int,
    caption: str,
) -> None:
    edit = getattr(gateway, "edit_message_caption", None)
    if edit is None:
        return
    try:
        await edit(chat_id=chat_id, message_id=message_id, caption=caption, buttons=None)
    except Exception:
        logger.warning(
            "venmo_confirmation_callback_message_edit_failed",
            exc_info=True,
            extra={"telegram_chat_id": chat_id, "telegram_message_id": message_id},
        )

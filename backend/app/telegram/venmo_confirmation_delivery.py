from __future__ import annotations

import asyncio
import random
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.repositories.venmo_confirmation import VenmoConfirmationRepository
from app.db.session import SessionFactory
from app.models.media_asset import MediaAsset
from app.models.venmo_confirmation import (
    VenmoConfirmationAttempt,
    VenmoConfirmationEventType,
    VenmoConfirmationRequest,
)
from app.services.venmo_confirmation import (
    VenmoConfirmationNotFoundError,
    VenmoConfirmationService,
)
from app.telegram.cashout_bot.api import (
    TelegramBotApiError,
    TelegramBotApiGateway,
    TelegramBotFailureClass,
)
from app.telegram.peer_ids import normalize_telegram_chat_id
from app.telegram.venmo_confirmation import venmo_confirmation_buttons
from app.websocket.events import LiveEventType, event_broker

logger = get_logger(__name__)

VENMO_CONFIRMATION_RETRY_DELAYS_SECONDS = (0.0, 2.0, 5.0, 10.0, 20.0, 40.0)
VENMO_CONFIRMATION_LEASE_SECONDS = 90
VENMO_CONFIRMATION_WORKER_POLL_SECONDS = 2

GatewayFactory = Callable[[], Any]
SleepFunc = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class VenmoConfirmationDeliveryResult:
    status: str
    request_id: int
    attempt_id: int
    delivery_attempts: int
    last_error: str | None = None


def classify_legacy_venmo_delivery_error(error_text: str | None) -> str:
    """Classify stored legacy send errors without retrying unknown failures."""
    if not error_text:
        return TelegramBotFailureClass.NON_RETRYABLE.value
    normalized = error_text.casefold()
    permanent_markers = (
        "bad request",
        "chat not found",
        "forbidden",
        "bot was blocked",
        "bot was kicked",
        "not enough rights",
        "unauthorized",
        "invalid token",
        "malformed",
        "wrong file identifier",
        "file is too big",
        "telegram_cashout_group_id is required",
        "telegram_venmo_group_id is required",
        "telegram_bot_token is required",
        "media is not available",
    )
    if any(marker in normalized for marker in permanent_markers):
        if any(marker in normalized for marker in ("unauthorized", "forbidden", "token")):
            return TelegramBotFailureClass.CONFIGURATION.value
        return TelegramBotFailureClass.NON_RETRYABLE.value
    retryable_markers = (
        "timed out",
        "timeout",
        "temporarily unavailable",
        "temporary failure",
        "transport error",
        "network",
        "connection reset",
        "connection aborted",
        "socket",
        "dns",
        "name resolution",
        "rate limit",
        "rate limited",
        "too many requests",
        "retry_after",
        "internal server error",
        "bad gateway",
        "service unavailable",
        "gateway timeout",
        " 500",
        " 502",
        " 503",
        " 504",
    )
    if any(marker in normalized for marker in retryable_markers):
        return TelegramBotFailureClass.RETRYABLE.value
    return TelegramBotFailureClass.NON_RETRYABLE.value


async def send_confirmation_attempt_with_retries(
    *,
    service: VenmoConfirmationService,
    request: VenmoConfirmationRequest,
    media: MediaAsset,
    attempt: VenmoConfirmationAttempt,
    event_type: VenmoConfirmationEventType,
    gateway_factory: GatewayFactory = TelegramBotApiGateway,
    sleep: SleepFunc = asyncio.sleep,
    retry_delays_seconds: tuple[float, ...] = VENMO_CONFIRMATION_RETRY_DELAYS_SECONDS,
    jitter_ratio: float = 0.2,
    stop_after_scheduling_retry: bool = False,
) -> VenmoConfirmationDeliveryResult:
    settings = get_settings()
    chat_id = normalize_telegram_chat_id(settings.resolved_venmo_telegram_group_id)
    if chat_id is None:
        await service.mark_attempt_failed(
            attempt_id=attempt.id,
            coadmin_id=request.coadmin_id,
            error=(
                "TELEGRAM_VENMO_GROUP_ID or TELEGRAM_CASHOUT_GROUP_ID is required "
                "to send confirmation requests."
            ),
        )
        logger.error(
            "venmo_confirmation_delivery_configuration_failed",
            extra={
                "venmo_confirmation_request_id": request.id,
                "venmo_confirmation_attempt_id": attempt.id,
                "coadmin_id": request.coadmin_id,
            },
        )
        return VenmoConfirmationDeliveryResult(
            status="failed",
            request_id=request.id,
            attempt_id=attempt.id,
            delivery_attempts=attempt.delivery_attempts,
            last_error=attempt.last_error,
        )

    for retry_index, base_delay in enumerate(retry_delays_seconds):
        if retry_index > 0:
            await sleep(base_delay)

        attempt = await service.begin_attempt_delivery(
            attempt_id=attempt.id,
            coadmin_id=request.coadmin_id,
            lease_seconds=VENMO_CONFIRMATION_LEASE_SECONDS,
        )
        if attempt.telegram_message_id is not None:
            logger.info(
                "venmo_confirmation_delivery_already_posted",
                extra=_log_extra(request, attempt, retry_number=retry_index),
            )
            return VenmoConfirmationDeliveryResult(
                status="already_posted",
                request_id=request.id,
                attempt_id=attempt.id,
                delivery_attempts=attempt.delivery_attempts,
            )

        try:
            logger.info(
                "venmo_confirmation_delivery_send_started",
                extra={
                    **_log_extra(request, attempt, retry_number=retry_index),
                    "telegram_chat_id": chat_id,
                    "venmo_group_fallback_to_cashout": (
                        settings.venmo_group_falls_back_to_cashout
                    ),
                },
            )
            if retry_index == 0 and settings.venmo_group_falls_back_to_cashout:
                logger.warning(
                    "venmo_telegram_group_falling_back_to_cashout_group",
                    extra={
                        "telegram_chat_id": chat_id,
                        "venmo_confirmation_request_id": request.id,
                        "venmo_confirmation_attempt_id": attempt.id,
                        "venmo_group_fallback_to_cashout": True,
                    },
                )
            async with gateway_factory() as gateway:
                caption = await service.render_venmo_confirmation_card(request, attempt)
                message_id = await gateway.send_photo(
                    chat_id=chat_id,
                    photo_path=_media_path(media.storage_key),
                    caption=caption.caption,
                    buttons=venmo_confirmation_buttons(attempt.id),
                    mime_type=media.mime_type,
                    filename=media.original_filename,
                )
                if caption.overflow_text:
                    send_overflow = getattr(gateway, "send_message", None)
                    if callable(send_overflow):
                        try:
                            await send_overflow(
                                chat_id=chat_id,
                                text=caption.overflow_text,
                                reply_to_message_id=message_id,
                            )
                        except Exception:
                            logger.warning(
                                "venmo_confirmation_note_overflow_followup_failed",
                                extra=_log_extra(request, attempt, retry_number=retry_index),
                                exc_info=True,
                            )
            if message_id is None:
                raise TelegramBotApiError(
                    "Telegram Bot API did not return a message_id.",
                    failure_class=TelegramBotFailureClass.RETRYABLE,
                )
            attempt = await service.mark_attempt_posted(
                attempt_id=attempt.id,
                coadmin_id=request.coadmin_id,
                telegram_chat_id=chat_id,
                telegram_message_id=message_id,
                event_type=event_type,
            )
            logger.info(
                "venmo_confirmation_delivery_send_succeeded",
                extra={
                    **_log_extra(request, attempt, retry_number=retry_index),
                    "telegram_chat_id": chat_id,
                    "telegram_message_id": message_id,
                    "outcome": "posted",
                },
            )
            await _publish_venmo_confirmation_update(request.id)
            return VenmoConfirmationDeliveryResult(
                status="posted",
                request_id=request.id,
                attempt_id=attempt.id,
                delivery_attempts=attempt.delivery_attempts,
            )
        except Exception as error:
            failure_class = _failure_class(error)
            retryable = failure_class == TelegramBotFailureClass.RETRYABLE.value
            final_try = retry_index >= len(retry_delays_seconds) - 1
            if not retryable or final_try:
                attempt = await service.mark_attempt_failed(
                    attempt_id=attempt.id,
                    coadmin_id=request.coadmin_id,
                    error=str(error),
                )
                logger.exception(
                    "venmo_confirmation_delivery_send_failed_terminal",
                    extra={
                        **_log_extra(request, attempt, retry_number=retry_index),
                        "failure_class": failure_class,
                        "telegram_status_code": _telegram_status_code(error),
                        "retry_after_seconds": _retry_after_seconds(error),
                        "outcome": "failed_to_send",
                    },
                )
                await _publish_venmo_confirmation_update(request.id)
                return VenmoConfirmationDeliveryResult(
                    status="failed",
                    request_id=request.id,
                    attempt_id=attempt.id,
                    delivery_attempts=attempt.delivery_attempts,
                    last_error=attempt.last_error,
                )

            delay_seconds = _retry_delay_seconds(
                error,
                retry_delays_seconds[retry_index + 1],
                jitter_ratio=jitter_ratio,
            )
            next_retry_at = datetime.now(UTC) + timedelta(seconds=delay_seconds)
            attempt = await service.record_attempt_retry(
                attempt_id=attempt.id,
                coadmin_id=request.coadmin_id,
                error=str(error),
                failure_class=failure_class,
                retry_number=retry_index + 1,
                delay_seconds=delay_seconds,
                next_retry_at=next_retry_at,
                telegram_status_code=_telegram_status_code(error),
            )
            logger.warning(
                "venmo_confirmation_delivery_retry_scheduled",
                extra={
                    **_log_extra(request, attempt, retry_number=retry_index + 1),
                    "failure_class": failure_class,
                    "telegram_status_code": _telegram_status_code(error),
                    "retry_after_seconds": delay_seconds,
                    "outcome": "retrying",
                },
            )
            await _publish_venmo_confirmation_update(request.id)
            if stop_after_scheduling_retry:
                return VenmoConfirmationDeliveryResult(
                    status="retrying",
                    request_id=request.id,
                    attempt_id=attempt.id,
                    delivery_attempts=attempt.delivery_attempts,
                    last_error=attempt.last_error,
                )

    return VenmoConfirmationDeliveryResult(
        status="failed",
        request_id=request.id,
        attempt_id=attempt.id,
        delivery_attempts=attempt.delivery_attempts,
        last_error=attempt.last_error,
    )


async def run_venmo_confirmation_delivery_worker(
    *,
    gateway_factory: GatewayFactory = TelegramBotApiGateway,
) -> None:
    logger.info("venmo_confirmation_delivery_worker_started")
    try:
        while True:
            processed = await deliver_next_due_venmo_confirmation(
                gateway_factory=gateway_factory
            )
            if not processed:
                await asyncio.sleep(VENMO_CONFIRMATION_WORKER_POLL_SECONDS)
    finally:
        logger.info("venmo_confirmation_delivery_worker_stopped")


async def deliver_next_due_venmo_confirmation(
    *,
    gateway_factory: GatewayFactory = TelegramBotApiGateway,
) -> bool:
    async with SessionFactory() as session:
        service = VenmoConfirmationService(session)
        repository = VenmoConfirmationRepository(session)
        due = await repository.claim_next_due_delivery(datetime.now(UTC))
        if due is None:
            return False
        request, media, attempt = due
        try:
            await send_confirmation_attempt_with_retries(
                service=service,
                request=request,
                media=media,
                attempt=attempt,
                event_type=VenmoConfirmationEventType.ATTEMPT_POSTED,
                gateway_factory=gateway_factory,
            )
        except VenmoConfirmationNotFoundError:
            logger.info(
                "venmo_confirmation_delivery_request_deleted",
                extra={
                    "venmo_confirmation_request_id": request.id,
                    "venmo_confirmation_attempt_id": attempt.id,
                },
            )
        await session.commit()
    return True


def _failure_class(error: Exception) -> str:
    if isinstance(error, TelegramBotApiError):
        return error.failure_class.value
    if isinstance(error, RuntimeError):
        return TelegramBotFailureClass.CONFIGURATION.value
    return TelegramBotFailureClass.RETRYABLE.value


def _telegram_status_code(error: Exception) -> int | None:
    if isinstance(error, TelegramBotApiError):
        return error.status_code
    return None


def _retry_after_seconds(error: Exception) -> int | None:
    if isinstance(error, TelegramBotApiError):
        return error.retry_after_seconds
    return None


def _retry_delay_seconds(
    error: Exception,
    base_delay: float,
    *,
    jitter_ratio: float,
) -> float:
    retry_after = _retry_after_seconds(error)
    if retry_after is not None:
        return float(retry_after)
    if base_delay <= 0 or jitter_ratio <= 0:
        return base_delay
    spread = base_delay * jitter_ratio
    return max(0.0, base_delay + random.uniform(-spread, spread))


def _media_path(storage_key: str) -> Path:
    root = Path(get_settings().inquiry_media_dir).resolve()
    path = (root / storage_key).resolve()
    if not path.is_relative_to(root):
        raise RuntimeError("Media is not available")
    return path


def _log_extra(
    request: VenmoConfirmationRequest,
    attempt: VenmoConfirmationAttempt,
    *,
    retry_number: int,
) -> dict[str, object]:
    return {
        "venmo_confirmation_request_id": request.id,
        "venmo_confirmation_attempt_id": attempt.id,
        "attempt_number": attempt.attempt_number,
        "retry_number": retry_number,
        "coadmin_id": request.coadmin_id,
    }


async def _publish_venmo_confirmation_update(request_id: int) -> None:
    await event_broker.publish(
        LiveEventType.VENMO_CONFIRMATION_UPDATED,
        venmo_confirmation_request_id=request_id,
        broadcast=True,
    )

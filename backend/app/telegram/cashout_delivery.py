from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from telethon import TelegramClient, errors  # type: ignore[import-untyped]
from telethon.tl import types  # type: ignore[import-untyped]
from telethon.tl.functions.messages import (  # type: ignore[import-untyped]
    SendMessageRequest,
)

from app.core.logging import get_logger
from app.db.repositories.cashout import CashoutRepository
from app.db.session import SessionFactory
from app.models.cashout import (
    CashoutAuditAction,
    CashoutRequestAudit,
    CashoutStatus,
    CashoutTelegramStatus,
    CashoutType,
)
from app.models.media_asset import MediaAsset
from app.models.user import User
from app.services.cashout_media import resolve_cashout_media_path
from app.telegram.cashout_bot.api import TelegramBotApiError, TelegramBotFailureClass
from app.telegram.cashout_bot.messages import (
    CashoutTaskView,
    build_active_task_markup,
    format_cashout_task_card,
    format_qr_cashout_caption,
)
from app.telegram.inquiry_ingestion import register_cashout_panel_message
from app.websocket.events import LiveEventType, event_broker

logger = get_logger(__name__)
DELIVERY_POLL_SECONDS = 2
DELIVERY_LEASE_SECONDS = 60
FAILED_STATUS_AFTER_ATTEMPTS = 3


@dataclass(frozen=True, slots=True)
class CashoutDelivery:
    """Detached payload claimed from the durable Telegram outbox."""

    cashout_id: int
    request_number: str
    player_tag: str
    cashout_type: CashoutType
    amount: Decimal
    notes: str | None
    requested_by: str
    created_at: datetime
    random_id: int
    attempt: int
    telegram_message_id: int | None = None
    qr_media_storage_key: str | None = None
    qr_media_mime_type: str | None = None
    qr_media_filename: str | None = None


def format_cashout_message(delivery: CashoutDelivery) -> str:
    """Create the stable human-readable Telegram cashout message."""
    lines = [
        "🔴 CASHOUT REQUEST",
        "",
        "Tag:",
        delivery.player_tag,
        "",
        "Amount:",
        f"${delivery.amount:,.2f}",
        "",
        "Requested By:",
        delivery.requested_by,
        "",
        "Time:",
        delivery.created_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC"),
        "",
        "Request ID:",
        delivery.request_number,
    ]
    if delivery.notes:
        lines.extend(["", "Optional Notes:", delivery.notes])
    return "\n".join(lines)


async def run_cashout_delivery_worker(
    client: TelegramClient,
    group_input: Any,
    *,
    telegram_chat_id: int | None = None,
    bot_gateway: Any | None = None,
) -> None:
    """Continuously drain pending cashouts through the existing client."""
    logger.info(
        "cashout_delivery_worker_started",
        extra={"telegram_chat_id": telegram_chat_id},
    )
    try:
        while True:
            try:
                processed = await deliver_next_cashout(
                    client,
                    group_input,
                    telegram_chat_id=telegram_chat_id,
                    bot_gateway=bot_gateway,
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception(
                    "cashout_delivery_worker_iteration_failed",
                    extra={"telegram_chat_id": telegram_chat_id},
                )
                await asyncio.sleep(DELIVERY_POLL_SECONDS)
                continue
            if not processed:
                await asyncio.sleep(DELIVERY_POLL_SECONDS)
    finally:
        logger.info("cashout_delivery_worker_stopped")


async def deliver_cashout_by_id(
    cashout_id: int,
    *,
    telegram_chat_id: int,
    bot_gateway: Any,
    client: Any | None = None,
    group_input: Any | None = None,
) -> bool:
    """Claim and deliver one already-persisted cashout without creating another."""
    return await deliver_next_cashout(
        client if client is not None else object(),
        group_input,
        telegram_chat_id=telegram_chat_id,
        bot_gateway=bot_gateway,
        cashout_id=cashout_id,
    )


async def deliver_next_cashout(
    client: TelegramClient,
    group_input: Any,
    *,
    telegram_chat_id: int | None = None,
    bot_gateway: Any | None = None,
    cashout_id: int | None = None,
) -> bool:
    """Claim and deliver one due outbox row."""
    delivery = await _claim_delivery(cashout_id=cashout_id)
    if delivery is None:
        return False

    logger.info(
        "cashout_telegram_send_started",
        extra={
            "cashout_request_id": delivery.cashout_id,
            "cashout_attempt": delivery.attempt,
            "telegram_chat_id": telegram_chat_id,
            "already_has_telegram_message": delivery.telegram_message_id is not None,
        },
    )
    try:
        if delivery.telegram_message_id is not None:
            await _record_success(
                delivery,
                delivery.telegram_message_id,
                telegram_chat_id=telegram_chat_id,
            )
            return True
        if bot_gateway is not None:
            if telegram_chat_id is None:
                raise RuntimeError("telegram_chat_id is required for bot delivery")
            buttons = build_active_task_markup(delivery.cashout_id)
            if delivery.cashout_type == CashoutType.QR:
                if not delivery.qr_media_storage_key or not delivery.qr_media_mime_type:
                    raise RuntimeError("QR cashout delivery is missing persisted media")
                view = CashoutTaskView(
                    cashout_id=delivery.cashout_id,
                    request_number=delivery.request_number,
                    player_tag=delivery.player_tag,
                    requested_amount=delivery.amount,
                    status=CashoutStatus.PENDING,
                    requested_by=delivery.requested_by,
                    created_at=delivery.created_at.astimezone(UTC),
                )
                message_id = await bot_gateway.send_photo(
                    chat_id=telegram_chat_id,
                    photo_path=resolve_cashout_media_path(delivery.qr_media_storage_key),
                    caption=format_qr_cashout_caption(view),
                    buttons=buttons,
                    mime_type=delivery.qr_media_mime_type,
                    filename=delivery.qr_media_filename,
                )
            else:
                message_id = await bot_gateway.send_cashout_task_message(
                    chat_id=telegram_chat_id,
                    text=format_cashout_task_card(
                        CashoutTaskView(
                            cashout_id=delivery.cashout_id,
                            request_number=delivery.request_number,
                            player_tag=delivery.player_tag,
                            requested_amount=delivery.amount,
                            status=CashoutStatus.PENDING,
                            requested_by=delivery.requested_by,
                            created_at=delivery.created_at.astimezone(UTC),
                            notes=delivery.notes,
                        )
                    ),
                    buttons=buttons,
                )
        else:
            request = SendMessageRequest(
                peer=group_input,
                message=format_cashout_message(delivery),
                no_webpage=True,
                random_id=delivery.random_id,
            )
            result = await client(request)
            message_id = _extract_message_id(result, delivery.random_id)
        await _record_success(
            delivery,
            message_id,
            telegram_chat_id=telegram_chat_id,
        )
    except errors.RandomIdDuplicateError:
        # Telegram already accepted this persisted random_id before the
        # application could record success (for example, during a crash).
        message_id = await _recover_message_id(client, group_input, delivery)
        await _record_success(
            delivery,
            message_id,
            telegram_chat_id=telegram_chat_id,
        )
        logger.info(
            "cashout_telegram_duplicate_confirmed",
            extra={
                "cashout_request_id": delivery.cashout_id,
                "cashout_attempt": delivery.attempt,
                "telegram_message_id": message_id,
                "telegram_chat_id": telegram_chat_id,
                "recovered_message_id": message_id is not None,
            },
        )
    except Exception as error:
        await _record_failure(delivery, error)
        logger.exception(
            "cashout_telegram_send_failed",
            extra={
                "cashout_request_id": delivery.cashout_id,
                "cashout_attempt": delivery.attempt,
                "telegram_chat_id": telegram_chat_id,
                "failure_class": _failure_class(error),
                "telegram_status_code": _telegram_status_code(error),
                "retry_after_seconds": _retry_after_seconds(error),
            },
        )
    return True


async def _claim_delivery(cashout_id: int | None = None) -> CashoutDelivery | None:
    now = datetime.now(UTC)
    async with SessionFactory() as session, session.begin():
        repository = CashoutRepository(session)
        cashout = await repository.claim_next_delivery(now, cashout_id=cashout_id)
        if cashout is None:
            return None

        previous_attempts = cashout.telegram_attempts
        cashout.telegram_attempts += 1
        cashout.telegram_next_attempt_at = now + timedelta(
            seconds=DELIVERY_LEASE_SECONDS
        )
        if previous_attempts > 0:
            await repository.add_audit(
                CashoutRequestAudit(
                    cashout_request_id=cashout.id,
                    action=CashoutAuditAction.TELEGRAM_RETRY,
                    actor_user_id=None,
                    previous_value={"telegram_attempts": previous_attempts},
                    new_value={
                        "telegram_attempts": cashout.telegram_attempts,
                        "automatic": True,
                    },
                )
            )
        username = await session.scalar(
            select(User.username).where(User.id == cashout.created_by_staff_id)
        )
        qr_media: MediaAsset | None = None
        if cashout.cashout_type == CashoutType.QR and cashout.qr_media_asset_id is not None:
            qr_media = await session.get(MediaAsset, cashout.qr_media_asset_id)
        if username is None or cashout.request_number is None:
            cashout.telegram_status = CashoutTelegramStatus.FAILED_TO_SEND
            if cashout.status == CashoutStatus.PENDING:
                cashout.status = CashoutStatus.FAILED_TO_SEND
            cashout.telegram_last_error = (
                "Cashout delivery references incomplete request data"
            )
            cashout.telegram_next_attempt_at = None
            logger.error(
                "cashout_telegram_claim_incomplete",
                extra={"cashout_request_id": cashout.id},
            )
            return None
        if cashout.cashout_type == CashoutType.QR and qr_media is None:
            cashout.telegram_status = CashoutTelegramStatus.FAILED_TO_SEND
            if cashout.status == CashoutStatus.PENDING:
                cashout.status = CashoutStatus.FAILED_TO_SEND
            cashout.telegram_last_error = "QR cashout delivery references missing media"
            cashout.telegram_next_attempt_at = None
            logger.error(
                "cashout_telegram_claim_missing_qr_media",
                extra={"cashout_request_id": cashout.id},
            )
            return None
        return CashoutDelivery(
            cashout_id=cashout.id,
            request_number=cashout.request_number,
            player_tag=cashout.player_tag,
            cashout_type=cashout.cashout_type,
            amount=cashout.amount,
            notes=cashout.notes,
            requested_by=username,
            created_at=cashout.created_at,
            random_id=cashout.telegram_random_id,
            attempt=cashout.telegram_attempts,
            telegram_message_id=cashout.telegram_message_id,
            qr_media_storage_key=qr_media.storage_key if qr_media is not None else None,
            qr_media_mime_type=qr_media.mime_type if qr_media is not None else None,
            qr_media_filename=qr_media.original_filename if qr_media is not None else None,
        )


async def _record_success(
    delivery: CashoutDelivery,
    message_id: int | None,
    *,
    telegram_chat_id: int | None = None,
) -> None:
    now = datetime.now(UTC)
    already_sent = False
    async with SessionFactory() as session, session.begin():
        repository = CashoutRepository(session)
        cashout = await repository.get_by_id_for_update(delivery.cashout_id)
        if cashout is None:
            return
        if cashout.telegram_status == CashoutTelegramStatus.SENT:
            already_sent = True
            if message_id is not None and cashout.telegram_message_id is None:
                cashout.telegram_message_id = message_id
                if telegram_chat_id is not None:
                    cashout.telegram_chat_id = telegram_chat_id
                await repository.add_audit(
                    CashoutRequestAudit(
                        cashout_request_id=cashout.id,
                        action=CashoutAuditAction.TELEGRAM_SENT,
                        actor_user_id=None,
                        previous_value={"telegram_message_id": None},
                        new_value={
                            "telegram_message_id": message_id,
                            "telegram_chat_id": telegram_chat_id,
                            "recovered": True,
                        },
                    )
                )
                logger.info(
                    "cashout_telegram_message_id_backfilled",
                    extra={
                        "cashout_request_id": delivery.cashout_id,
                        "telegram_message_id": message_id,
                        "telegram_chat_id": telegram_chat_id,
                    },
                )
            elif telegram_chat_id is not None and cashout.telegram_chat_id is None:
                cashout.telegram_chat_id = telegram_chat_id
        else:
            previous = {
                "telegram_status": cashout.telegram_status.value,
                "status": cashout.status.value,
            }
            cashout.telegram_status = CashoutTelegramStatus.SENT
            cashout.telegram_message_id = message_id
            cashout.telegram_chat_id = telegram_chat_id
            cashout.telegram_sent_at = now
            cashout.telegram_next_attempt_at = None
            cashout.telegram_last_error = None
            if cashout.status in (
                CashoutStatus.PENDING,
                CashoutStatus.FAILED_TO_SEND,
            ):
                cashout.status = CashoutStatus.SENT
            await repository.add_audit(
                CashoutRequestAudit(
                    cashout_request_id=cashout.id,
                    action=CashoutAuditAction.TELEGRAM_SENT,
                    actor_user_id=None,
                    previous_value=previous,
                    new_value={
                        "telegram_status": cashout.telegram_status.value,
                        "status": cashout.status.value,
                        "telegram_message_id": message_id,
                        "telegram_chat_id": telegram_chat_id,
                    },
                )
            )

    if not already_sent:
        await event_broker.publish(
            LiveEventType.CASHOUT_SENT,
            cashout_id=delivery.cashout_id,
            broadcast=True,
        )
    if message_id is None and not already_sent:
        logger.warning(
            "cashout_telegram_message_id_missing",
            extra={
                "cashout_request_id": delivery.cashout_id,
                "cashout_attempt": delivery.attempt,
            },
        )
    if not already_sent:
        logger.info(
            "cashout_telegram_send_succeeded",
            extra={
                "cashout_request_id": delivery.cashout_id,
                "cashout_attempt": delivery.attempt,
                "telegram_message_id": message_id,
                "telegram_chat_id": telegram_chat_id,
            },
        )
    if message_id is not None and telegram_chat_id is not None:
        await register_cashout_panel_message(
            telegram_chat_id=telegram_chat_id,
            telegram_message_id=message_id,
            text=format_cashout_message(delivery),
        )


async def _record_failure(
    delivery: CashoutDelivery,
    error: Exception,
) -> None:
    now = datetime.now(UTC)
    failure_class = _failure_class(error)
    retry_after = _retry_after_seconds(error)
    retry_seconds = retry_after if retry_after is not None else min(
        300,
        2 ** min(delivery.attempt, 8) * 2,
    )
    async with SessionFactory() as session, session.begin():
        repository = CashoutRepository(session)
        cashout = await repository.get_by_id_for_update(delivery.cashout_id)
        if cashout is None or cashout.telegram_status == CashoutTelegramStatus.SENT:
            return
        cashout.telegram_last_error = str(error)[:2000]
        if failure_class in {
            TelegramBotFailureClass.NON_RETRYABLE.value,
            TelegramBotFailureClass.CONFIGURATION.value,
        }:
            cashout.telegram_status = CashoutTelegramStatus.FAILED_TO_SEND
            if cashout.status == CashoutStatus.PENDING:
                cashout.status = CashoutStatus.FAILED_TO_SEND
            cashout.telegram_next_attempt_at = None
        else:
            cashout.telegram_next_attempt_at = now + timedelta(seconds=retry_seconds)
            if cashout.telegram_attempts >= FAILED_STATUS_AFTER_ATTEMPTS:
                cashout.telegram_status = CashoutTelegramStatus.FAILED_TO_SEND
                if cashout.status == CashoutStatus.PENDING:
                    cashout.status = CashoutStatus.FAILED_TO_SEND
            else:
                cashout.telegram_status = CashoutTelegramStatus.PENDING
        await repository.add_audit(
            CashoutRequestAudit(
                cashout_request_id=cashout.id,
                action=CashoutAuditAction.TELEGRAM_RETRY,
                actor_user_id=None,
                previous_value={"telegram_status": cashout.telegram_status.value},
                new_value={
                    "failure_class": failure_class,
                    "retry_after_seconds": retry_seconds,
                    "terminal": cashout.telegram_next_attempt_at is None,
                },
            )
        )


def _extract_message_id(result: Any, random_id: int) -> int | None:
    direct_id = getattr(result, "id", None)
    if isinstance(direct_id, int):
        return direct_id

    updates = getattr(result, "updates", ())
    for update in updates:
        if (
            isinstance(update, types.UpdateMessageID)
            and update.random_id == random_id
        ):
            return int(update.id)
    for update in updates:
        message = getattr(update, "message", None)
        message_id = getattr(message, "id", None)
        if isinstance(message_id, int):
            return message_id
    return None


def _failure_class(error: Exception) -> str:
    if isinstance(error, TelegramBotApiError):
        return error.failure_class.value
    if isinstance(error, asyncio.TimeoutError):
        return TelegramBotFailureClass.RETRYABLE.value
    return TelegramBotFailureClass.RETRYABLE.value


def _telegram_status_code(error: Exception) -> int | None:
    if isinstance(error, TelegramBotApiError):
        return error.status_code
    return None


def _retry_after_seconds(error: Exception) -> int | None:
    if isinstance(error, TelegramBotApiError):
        return error.retry_after_seconds
    return None


async def _recover_message_id(
    client: TelegramClient,
    group_input: Any,
    delivery: CashoutDelivery,
) -> int | None:
    """Best-effort lookup for a cashout message after duplicate-send recovery."""
    try:
        messages = await client.get_messages(group_input, limit=25)
    except Exception:
        logger.exception(
            "cashout_telegram_message_id_recovery_failed",
            extra={
                "cashout_request_id": delivery.cashout_id,
                "cashout_attempt": delivery.attempt,
            },
        )
        return None

    request_marker = f"Request ID:\n{delivery.request_number}"
    for message in messages:
        text = getattr(message, "message", None) or getattr(message, "text", None)
        message_id = getattr(message, "id", None)
        if not isinstance(message_id, int) or not isinstance(text, str):
            continue
        if delivery.request_number in text or request_marker in text:
            logger.info(
                "cashout_telegram_message_id_recovered",
                extra={
                    "cashout_request_id": delivery.cashout_id,
                    "telegram_message_id": message_id,
                },
            )
            return message_id

    logger.warning(
        "cashout_telegram_message_id_recovery_not_found",
        extra={
            "cashout_request_id": delivery.cashout_id,
            "request_number": delivery.request_number,
        },
    )
    return None

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
)
from app.models.user import User
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
    amount: Decimal
    notes: str | None
    requested_by: str
    created_at: datetime
    random_id: int
    attempt: int


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
) -> None:
    """Continuously drain pending cashouts through the existing client."""
    logger.info("cashout_delivery_worker_started")
    try:
        while True:
            processed = await deliver_next_cashout(client, group_input)
            if not processed:
                await asyncio.sleep(DELIVERY_POLL_SECONDS)
    finally:
        logger.info("cashout_delivery_worker_stopped")


async def deliver_next_cashout(
    client: TelegramClient,
    group_input: Any,
) -> bool:
    """Claim and deliver one due outbox row."""
    delivery = await _claim_delivery()
    if delivery is None:
        return False

    request = SendMessageRequest(
        peer=group_input,
        message=format_cashout_message(delivery),
        no_webpage=True,
        random_id=delivery.random_id,
    )
    try:
        result = await client(request)
        message_id = _extract_message_id(result, delivery.random_id)
        await _record_success(delivery, message_id)
    except errors.RandomIdDuplicateError:
        # Telegram already accepted this persisted random_id before the
        # application could record success (for example, during a crash).
        message_id = await _recover_message_id(client, group_input, delivery)
        await _record_success(delivery, message_id)
        logger.info(
            "cashout_telegram_duplicate_confirmed",
            extra={
                "cashout_request_id": delivery.cashout_id,
                "cashout_attempt": delivery.attempt,
                "telegram_message_id": message_id,
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
            },
        )
    return True


async def _claim_delivery() -> CashoutDelivery | None:
    now = datetime.now(UTC)
    async with SessionFactory() as session, session.begin():
        repository = CashoutRepository(session)
        cashout = await repository.claim_next_delivery(now)
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
        if username is None or cashout.request_number is None:
            raise RuntimeError("Cashout delivery references incomplete request data")
        return CashoutDelivery(
            cashout_id=cashout.id,
            request_number=cashout.request_number,
            player_tag=cashout.player_tag,
            amount=cashout.amount,
            notes=cashout.notes,
            requested_by=username,
            created_at=cashout.created_at,
            random_id=cashout.telegram_random_id,
            attempt=cashout.telegram_attempts,
        )


async def _record_success(
    delivery: CashoutDelivery,
    message_id: int | None,
) -> None:
    now = datetime.now(UTC)
    async with SessionFactory() as session, session.begin():
        repository = CashoutRepository(session)
        cashout = await repository.get_by_id_for_update(delivery.cashout_id)
        if cashout is None:
            return
        if cashout.telegram_status == CashoutTelegramStatus.SENT:
            if message_id is not None and cashout.telegram_message_id is None:
                cashout.telegram_message_id = message_id
                await repository.add_audit(
                    CashoutRequestAudit(
                        cashout_request_id=cashout.id,
                        action=CashoutAuditAction.TELEGRAM_SENT,
                        actor_user_id=None,
                        previous_value={"telegram_message_id": None},
                        new_value={"telegram_message_id": message_id, "recovered": True},
                    )
                )
                logger.info(
                    "cashout_telegram_message_id_backfilled",
                    extra={
                        "cashout_request_id": delivery.cashout_id,
                        "telegram_message_id": message_id,
                    },
                )
            return
        previous = {
            "telegram_status": cashout.telegram_status.value,
            "status": cashout.status.value,
        }
        cashout.telegram_status = CashoutTelegramStatus.SENT
        cashout.telegram_message_id = message_id
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
                },
            )
        )
    await event_broker.publish(
        LiveEventType.CASHOUT_SENT,
        cashout_id=delivery.cashout_id,
        broadcast=True,
    )
    if message_id is None:
        logger.warning(
            "cashout_telegram_message_id_missing",
            extra={
                "cashout_request_id": delivery.cashout_id,
                "cashout_attempt": delivery.attempt,
            },
        )
    logger.info(
        "cashout_telegram_send_succeeded",
        extra={
            "cashout_request_id": delivery.cashout_id,
            "cashout_attempt": delivery.attempt,
            "telegram_message_id": message_id,
        },
    )


async def _record_failure(
    delivery: CashoutDelivery,
    error: Exception,
) -> None:
    now = datetime.now(UTC)
    retry_seconds = min(300, 2 ** min(delivery.attempt, 8) * 2)
    async with SessionFactory() as session, session.begin():
        repository = CashoutRepository(session)
        cashout = await repository.get_by_id_for_update(delivery.cashout_id)
        if cashout is None or cashout.telegram_status == CashoutTelegramStatus.SENT:
            return
        cashout.telegram_last_error = str(error)[:2000]
        cashout.telegram_next_attempt_at = now + timedelta(seconds=retry_seconds)
        if cashout.telegram_attempts >= FAILED_STATUS_AFTER_ATTEMPTS:
            cashout.telegram_status = CashoutTelegramStatus.FAILED_TO_SEND
            if cashout.status == CashoutStatus.PENDING:
                cashout.status = CashoutStatus.FAILED_TO_SEND
        else:
            cashout.telegram_status = CashoutTelegramStatus.PENDING


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

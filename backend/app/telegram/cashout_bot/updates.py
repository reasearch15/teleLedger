from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.core.logging import get_logger
from app.db.session import SessionFactory
from app.services.cashout_telegram import (
    CashoutTelegramGateway,
    CashoutTelegramService,
)
from app.services.venmo_confirmation import VenmoConfirmationService
from app.telegram.cashout_bot.api import (
    TelegramBotApiError,
    TelegramBotFailureClass,
    TelegramBotUpdate,
)
from app.telegram.peer_ids import normalize_telegram_chat_id
from app.telegram.venmo_confirmation import decode_venmo_confirmation_callback

logger = get_logger(__name__)
TerminalReporter = Callable[[str], None]


async def run_cashout_bot_update_loop(
    gateway: CashoutTelegramGateway,
    *,
    session_factory: async_sessionmaker[Any] = SessionFactory,
    report: TerminalReporter = print,
) -> None:
    """Poll Telegram Bot API updates and route cashout interactions."""
    offset: int | None = None
    settings = get_settings()
    report("Listening for cashout bot callbacks.")
    await gateway.delete_webhook(drop_pending_updates=False)
    logger.info("cashout_bot_webhook_cleared_for_polling")
    while True:
        try:
            updates = await gateway.get_updates(offset=offset)
        except TelegramBotApiError as error:
            if error.failure_class != TelegramBotFailureClass.RETRYABLE:
                logger.exception(
                    "cashout_bot_update_poll_failed",
                    extra={
                        "failure_class": error.failure_class.value,
                        "telegram_status_code": error.status_code,
                    },
                )
                raise
            delay = float(error.retry_after_seconds or settings.telegram_bot_poll_seconds)
            logger.warning(
                "cashout_bot_update_poll_retryable_failed",
                extra={
                    "failure_class": error.failure_class.value,
                    "telegram_status_code": error.status_code,
                    "retry_delay_seconds": delay,
                },
            )
            await asyncio.sleep(delay)
            continue
        except Exception:
            logger.exception("cashout_bot_update_poll_failed")
            raise
        for update in updates:
            offset = update.update_id + 1
            logger.info("cashout_bot_update_received", extra={"update_id": update.update_id})
            try:
                await handle_cashout_bot_update(
                    update,
                    gateway=gateway,
                    session_factory=session_factory,
                    report=report,
                )
            except Exception:
                logger.exception(
                    "cashout_bot_update_route_failed",
                    extra={"update_id": update.update_id},
                )
                continue
        if not updates:
            await asyncio.sleep(settings.telegram_bot_poll_seconds)


async def handle_cashout_bot_update(
    update: TelegramBotUpdate,
    *,
    gateway: CashoutTelegramGateway,
    session_factory: async_sessionmaker[Any] = SessionFactory,
    report: TerminalReporter = print,
) -> None:
    payload = update.payload
    callback = payload.get("callback_query")
    if isinstance(callback, dict):
        logger.info("cashout_bot_callback_update_received")
        await _handle_callback(
            callback,
            gateway=gateway,
            session_factory=session_factory,
            report=report,
        )
        return

    message = payload.get("message")
    if isinstance(message, dict):
        logger.info("cashout_bot_message_update_received")
        await _handle_message(
            message,
            gateway=gateway,
            session_factory=session_factory,
            report=report,
        )


async def _handle_callback(
    callback: dict[str, Any],
    *,
    gateway: CashoutTelegramGateway,
    session_factory: async_sessionmaker[Any],
    report: TerminalReporter,
) -> None:
    message = callback.get("message")
    chat = message.get("chat") if isinstance(message, dict) else None
    from_user = callback.get("from")
    chat_id = _id_from(chat)
    message_id = _id_from(message)
    user_id = _id_from(from_user)
    callback_id = callback.get("id")
    data = callback.get("data")
    if (
        chat_id is None
        or message_id is None
        or user_id is None
        or callback_id is None
        or not isinstance(data, str)
    ):
        logger.info("cashout_bot_callback_ignored", extra={"reason_ignored": "missing_fields"})
        return

    if decode_venmo_confirmation_callback(data) is not None:
        async with session_factory() as session:
            service = VenmoConfirmationService(session)
            result = await service.handle_telegram_callback(
                query_id=str(callback_id),
                callback_data=data,
                telegram_chat_id=chat_id,
                telegram_user_id=user_id,
                telegram_username=_username_from(from_user),
                message_id=message_id,
                gateway=gateway,
            )
            await session.commit()
        logger.info(
            "venmo_confirmation_callback_routed",
            extra={
                "telegram_chat_id": chat_id,
                "telegram_message_id": message_id,
                "telegram_user_id": user_id,
                "callback_status": result.status,
                "venmo_confirmation_request_id": result.request_id,
                "venmo_confirmation_attempt_id": result.attempt_id,
            },
        )
        report(f"Venmo confirmation callback: {result.status}")
        return

    async with session_factory() as session:
        service = CashoutTelegramService(session, gateway=gateway)
        result = await service.handle_callback_query(
            query_id=callback_id,
            callback_data=data,
            telegram_chat_id=chat_id,
            telegram_user_id=user_id,
            telegram_username=_username_from(from_user),
            message_id=message_id,
        )
    logger.info(
        "cashout_bot_callback_routed",
        extra={
            "telegram_chat_id": chat_id,
            "telegram_message_id": message_id,
            "telegram_user_id": user_id,
            "callback_status": result.status,
        },
    )
    report(f"Cashout bot callback: {result.status}")


async def _handle_message(
    message: dict[str, Any],
    *,
    gateway: CashoutTelegramGateway,
    session_factory: async_sessionmaker[Any],
    report: TerminalReporter,
) -> None:
    chat = message.get("chat")
    from_user = message.get("from")
    chat_id = _id_from(chat)
    user_id = _id_from(from_user)
    text = message.get("text")
    if chat_id is None or user_id is None or not isinstance(text, str):
        return

    async with session_factory() as session:
        service = CashoutTelegramService(session, gateway=gateway)
        result = await service.handle_partial_amount_message(
            telegram_chat_id=chat_id,
            telegram_user_id=user_id,
            telegram_username=_username_from(from_user),
            text=text,
        )
    if result is not None:
        report(f"Cashout bot message: {result.status}")


def _id_from(value: object) -> int | None:
    if not isinstance(value, dict):
        return None
    raw = value.get("id") or value.get("message_id")
    if not isinstance(raw, int):
        return None
    if "type" in value:
        return normalize_telegram_chat_id(raw)
    return raw


def _username_from(value: object) -> str | None:
    if not isinstance(value, dict):
        return None
    username = value.get("username")
    if isinstance(username, str):
        return username
    first = value.get("first_name")
    last = value.get("last_name")
    parts = [part for part in (first, last) if isinstance(part, str) and part]
    return " ".join(parts) or None

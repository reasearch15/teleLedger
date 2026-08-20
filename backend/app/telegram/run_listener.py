from __future__ import annotations

import asyncio
from collections.abc import Callable

from telethon import events  # type: ignore[import-untyped]

import app.telegram.listener_health as listener_health
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.telegram.backfill import backfill_new_messages
from app.telegram.cashout_bot.api import TelegramBotApiGateway
from app.telegram.cashout_bot.updates import run_cashout_bot_update_loop
from app.telegram.cashout_delivery import run_cashout_delivery_worker
from app.telegram.client import create_telegram_client
from app.telegram.events import create_new_message_handler
from app.telegram.identity import telegram_display_name, telegram_entity_id
from app.telegram.ingestion import ingest_telegram_message
from app.telegram.inquiry_backfill import backfill_new_inquiry_messages
from app.telegram.inquiry_events import create_inquiry_message_handlers
from app.telegram.inquiry_ingestion import (
    ingest_inquiry_telegram_message,
    retry_pending_inquiry_media,
)
from app.telegram.peer_ids import normalize_telegram_chat_id
from app.telegram.venmo_confirmation_delivery import run_venmo_confirmation_delivery_worker

logger = get_logger(__name__)
TerminalReporter = Callable[[str], None]
RECONNECT_BASE_SECONDS = 2
RECONNECT_MAX_SECONDS = 60


def _session_file_name(session_name: str | None) -> str:
    if not session_name:
        return "<not configured>"
    return session_name if session_name.endswith(".session") else f"{session_name}.session"


def _print_startup_configuration(
    settings: Settings,
    report: TerminalReporter,
) -> None:
    report("Telegram Ledger listener")
    report(f"  TELEGRAM_ENABLED: {str(settings.telegram_enabled).lower()}")
    report(f"  Session name: {settings.telegram_session_name or '<not configured>'}")
    if settings.telegram_group_id is not None:
        report(f"  Payment group configured: yes (ID: {settings.telegram_group_id})")
    elif settings.telegram_group_username is not None:
        report(
            f"  Payment group configured: yes (username: {settings.telegram_group_username})"
        )
    else:
        report("  Payment group configured: no")
    if settings.telegram_cashout_group_id is not None:
        report(f"  Cashout group configured: yes (ID: {settings.telegram_cashout_group_id})")
    else:
        report("  Cashout group configured: no")
    if settings.telegram_venmo_group_id is not None:
        report(
            "  Venmo confirmation group configured: yes "
            f"(ID: {settings.telegram_venmo_group_id})"
        )
    elif settings.venmo_group_falls_back_to_cashout:
        report(
            "  Venmo confirmation group: FALLBACK to TELEGRAM_CASHOUT_GROUP_ID "
            f"(ID: {settings.telegram_cashout_group_id})"
        )
    else:
        report("  Venmo confirmation group configured: no")
    report("  Cashout completion reactions: disabled")
    report(f"  Cashout bot configured: {'yes' if settings.telegram_bot_token else 'no'}")


async def run_listener(report: TerminalReporter = print) -> None:
    """Start the Telegram listener with automatic reconnect and bot cashout runtime."""
    settings = get_settings()
    configure_logging(settings.log_level)
    _print_startup_configuration(settings, report)

    if not settings.telegram_enabled:
        report("Listener is disabled. Set TELEGRAM_ENABLED=true to connect.")
        logger.info("Telegram cashout/Venmo workflow not configured")
        return

    payment_group_target = settings.telegram_group_target
    if payment_group_target is None:
        raise RuntimeError(
            "TELEGRAM_GROUP_ID or TELEGRAM_GROUP_USERNAME is required when the listener is enabled"
        )
    cashout_group_target = settings.telegram_cashout_group_id
    if cashout_group_target is None:
        logger.error(
            "Telegram cashout workflow not configured",
            extra={"reason_ignored": "cashout_group_missing"},
        )
        raise RuntimeError(
            "TELEGRAM_CASHOUT_GROUP_ID is required for cashout Telegram delivery"
        )
    if settings.venmo_group_falls_back_to_cashout:
        logger.warning(
            "venmo_telegram_group_falling_back_to_cashout_group",
            extra={
                "telegram_chat_id": settings.telegram_cashout_group_id,
                "venmo_group_fallback_to_cashout": True,
            },
        )
    elif settings.resolved_venmo_telegram_group_id is not None:
        logger.info(
            "venmo_telegram_group_configured",
            extra={"telegram_chat_id": settings.resolved_venmo_telegram_group_id},
        )
    if settings.telegram_bot_token is None:
        logger.error(
            "Telegram cashout/Venmo workflow not configured",
            extra={"reason_ignored": "bot_token_missing"},
        )
        raise RuntimeError("TELEGRAM_BOT_TOKEN is required when the listener is enabled")

    reconnect_delay = RECONNECT_BASE_SECONDS
    while True:
        try:
            await _run_listener_session(
                settings,
                payment_group_target=payment_group_target,
                cashout_group_target=cashout_group_target,
                report=report,
            )
            reconnect_delay = RECONNECT_BASE_SECONDS
        except asyncio.CancelledError:
            raise
        except Exception:
            listener_health.mark_restart()
            logger.exception("telegram_listener_session_failed")
            report(f"Listener session failed; reconnecting in {reconnect_delay}s...")
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(RECONNECT_MAX_SECONDS, reconnect_delay * 2)
            continue

        listener_health.mark_restart()
        report(f"Telegram disconnected; reconnecting in {reconnect_delay}s...")
        logger.warning(
            "telegram_listener_disconnected_reconnecting",
            extra={"reconnect_delay_seconds": reconnect_delay},
        )
        await asyncio.sleep(reconnect_delay)
        reconnect_delay = min(RECONNECT_MAX_SECONDS, reconnect_delay * 2)


async def _run_listener_session(
    settings: Settings,
    *,
    payment_group_target: str | int,
    cashout_group_target: int,
    report: TerminalReporter,
) -> None:
    """Connect once, register handlers, and block until disconnected."""
    client = create_telegram_client(settings)
    handler = create_new_message_handler(ingest_telegram_message, report)
    edit_handler = create_new_message_handler(
        ingest_telegram_message,
        report,
        event_type="message_edited",
    )
    delivery_task: asyncio.Task[None] | None = None
    venmo_delivery_task: asyncio.Task[None] | None = None
    bot_update_task: asyncio.Task[None] | None = None
    bot_gateway: TelegramBotApiGateway | None = None

    try:
        await client.start()
        account = await client.get_me()
        payment_group = await client.get_entity(payment_group_target)
        cashout_group = await client.get_entity(cashout_group_target)
        report(
            f"Connected Telegram account: {telegram_display_name(account)} "
            f"(id={telegram_entity_id(account)})"
        )
        report(
            "Connected payment group: "
            f"{telegram_display_name(payment_group)} (id={telegram_entity_id(payment_group)})"
        )
        report(
            "Connected cashout group: "
            f"{telegram_display_name(cashout_group)} (id={telegram_entity_id(cashout_group)})"
        )
        report(f"Session file: {_session_file_name(settings.telegram_session_name)}")
        await backfill_new_messages(
            client,
            payment_group,
            limit=settings.telegram_backfill_limit,
            report=report,
        )
        await backfill_new_inquiry_messages(
            client,
            cashout_group,
            limit=settings.telegram_backfill_limit,
            report=report,
        )
        payment_group_input = await client.get_input_entity(payment_group)
        cashout_group_input = await client.get_input_entity(cashout_group)
        payment_group_chat_id = int(telegram_entity_id(payment_group))
        cashout_group_chat_id = normalize_telegram_chat_id(
            int(telegram_entity_id(cashout_group))
        )
        assert cashout_group_chat_id is not None

        listener_health.mark_connected(cashout_group_chat_id=cashout_group_chat_id)
        report("Cashout reaction completion disabled.")
        listener_health.mark_reconciliation(error=None)

        report("Retrying pending inquiry media...")
        media_recovered = await retry_pending_inquiry_media(
            client,
            cashout_group_input,
            limit=settings.inquiry_page_size_default,
        )
        if media_recovered:
            report(f"Recovered {media_recovered} inquiry media file(s).")

        async def ingest_cashout_group_message(message: object) -> None:
            await ingest_inquiry_telegram_message(message, client=client)

        inquiry_new_handler, inquiry_edit_handler, inquiry_delete_handler = (
            create_inquiry_message_handlers(
                ingest_message=ingest_cashout_group_message,
                report=report,
            )
        )
        client.add_event_handler(handler, events.NewMessage(chats=payment_group_input))
        client.add_event_handler(
            edit_handler,
            events.MessageEdited(chats=payment_group_input),
        )
        client.add_event_handler(
            inquiry_new_handler,
            events.NewMessage(chats=cashout_group_input),
        )
        client.add_event_handler(
            inquiry_edit_handler,
            events.MessageEdited(chats=cashout_group_input),
        )
        client.add_event_handler(
            inquiry_delete_handler,
            events.MessageDeleted(chats=cashout_group_input),
        )
        logger.info(
            "telegram_reaction_completion_disabled",
            extra={"telegram_group": cashout_group_chat_id},
        )
        bot_gateway = await TelegramBotApiGateway().__aenter__()
        delivery_task = asyncio.create_task(
            run_cashout_delivery_worker(
                client,
                cashout_group_input,
                telegram_chat_id=cashout_group_chat_id,
                bot_gateway=bot_gateway,
            ),
            name="cashout-delivery",
        )
        venmo_delivery_task = asyncio.create_task(
            run_venmo_confirmation_delivery_worker(),
            name="venmo-confirmation-delivery",
        )
        bot_update_task = asyncio.create_task(
            run_cashout_bot_update_loop(bot_gateway, report=report),
            name="cashout-bot-updates",
        )
        venmo_delivery_task.add_done_callback(_log_background_task_failure)
        bot_update_task.add_done_callback(_log_background_task_failure)
        report("Listening for new text messages and cashout bot actions. Press Ctrl+C to stop.")
        logger.info(
            "telegram_listener_connected",
            extra={
                "telegram_group": payment_group_chat_id,
                "cashout_telegram_group": cashout_group_chat_id,
                "venmo_telegram_group": settings.resolved_venmo_telegram_group_id,
                "venmo_group_fallback_to_cashout": (
                    settings.venmo_group_falls_back_to_cashout
                ),
            },
        )
        await client.run_until_disconnected()
    finally:
        listener_health.mark_disconnected()
        for task in (delivery_task, venmo_delivery_task, bot_update_task):
            if task is not None:
                task.cancel()
        await asyncio.gather(
            *(
                task
                for task in (delivery_task, venmo_delivery_task, bot_update_task)
                if task is not None
            ),
            return_exceptions=True,
        )
        if bot_gateway is not None:
            await bot_gateway.__aexit__(None, None, None)
        await client.disconnect()
        logger.info("telegram_listener_stopped")


def _log_background_task_failure(task: asyncio.Task[None]) -> None:
    if task.cancelled():
        return
    try:
        task.result()
    except Exception:
        logger.exception(
            "telegram_listener_background_task_failed",
            extra={"task_name": task.get_name()},
        )


def main() -> None:
    """CLI entry point."""
    try:
        asyncio.run(run_listener())
    except KeyboardInterrupt:
        print("Listener stopped.")


if __name__ == "__main__":
    main()

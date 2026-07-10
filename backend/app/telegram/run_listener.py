from __future__ import annotations

import asyncio
from collections.abc import Callable

from telethon import events  # type: ignore[import-untyped]

from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.telegram.backfill import backfill_new_messages
from app.telegram.cashout_delivery import run_cashout_delivery_worker
from app.telegram.cashout_reactions import complete_recent_cashout_reactions
from app.telegram.client import create_telegram_client
from app.telegram.events import create_new_message_handler, create_reaction_handler
from app.telegram.identity import telegram_display_name, telegram_entity_id
from app.telegram.ingestion import ingest_telegram_message

logger = get_logger(__name__)
TerminalReporter = Callable[[str], None]


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
        report(
            f"  Cashout group configured: yes (ID: {settings.telegram_cashout_group_id})"
        )
    else:
        report("  Cashout group configured: no")


async def run_listener(report: TerminalReporter = print) -> None:
    """Start the configured local Telegram listener until disconnected."""
    settings = get_settings()
    configure_logging(settings.log_level)
    _print_startup_configuration(settings, report)

    if not settings.telegram_enabled:
        report("Listener is disabled. Set TELEGRAM_ENABLED=true to connect.")
        logger.info("telegram_listener_disabled")
        return

    payment_group_target = settings.telegram_group_target
    if payment_group_target is None:
        raise RuntimeError(
            "TELEGRAM_GROUP_ID or TELEGRAM_GROUP_USERNAME is required when the listener is enabled"
        )
    cashout_group_target = settings.telegram_cashout_group_id
    if cashout_group_target is None:
        raise RuntimeError(
            "TELEGRAM_CASHOUT_GROUP_ID is required when the listener is enabled"
        )

    client = create_telegram_client(settings)
    handler = create_new_message_handler(ingest_telegram_message, report)
    delivery_task: asyncio.Task[None] | None = None

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
        payment_group_input = await client.get_input_entity(payment_group)
        cashout_group_input = await client.get_input_entity(cashout_group)
        payment_group_chat_id = int(telegram_entity_id(payment_group))
        cashout_group_chat_id = int(telegram_entity_id(cashout_group))
        reaction_handler = create_reaction_handler(
            expected_chat_id=cashout_group_chat_id,
            complete_recent_reactions=lambda: complete_recent_cashout_reactions(
                client,
                cashout_group_input,
                expected_chat_id=cashout_group_chat_id,
            ),
            report=report,
        )
        client.add_event_handler(handler, events.NewMessage(chats=payment_group_input))
        client.add_event_handler(reaction_handler, events.Raw())
        report("Listening for message reactions.")
        logger.info(
            "telegram_reaction_listener_subscribed",
            extra={"telegram_group": cashout_group_chat_id},
        )
        delivery_task = asyncio.create_task(
            run_cashout_delivery_worker(client, cashout_group_input),
            name="cashout-delivery",
        )
        report("Listening for new text messages. Press Ctrl+C to stop.")
        logger.info(
            "telegram_listener_connected",
            extra={
                "telegram_group": payment_group_chat_id,
                "cashout_telegram_group": cashout_group_chat_id,
            },
        )
        await client.run_until_disconnected()
    finally:
        if delivery_task is not None:
            delivery_task.cancel()
            await asyncio.gather(delivery_task, return_exceptions=True)
        await client.disconnect()
        logger.info("telegram_listener_stopped")


def main() -> None:
    """CLI entry point."""
    try:
        asyncio.run(run_listener())
    except KeyboardInterrupt:
        print("Listener stopped.")


if __name__ == "__main__":
    main()

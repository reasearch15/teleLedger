from __future__ import annotations

import asyncio
from collections.abc import Callable

from telethon import events  # type: ignore[import-untyped]

import app.telegram.listener_health as listener_health
from app.core.config import Settings, get_settings
from app.core.logging import configure_logging, get_logger
from app.telegram.backfill import backfill_new_messages
from app.telegram.cashout_delivery import run_cashout_delivery_worker
from app.telegram.cashout_reactions import complete_recent_cashout_reactions
from app.telegram.cashout_reconciliation import (
    reconcile_pending_cashout_reactions,
    run_cashout_reaction_reconciliation_loop,
)
from app.telegram.client import create_telegram_client
from app.telegram.events import create_new_message_handler, create_reaction_handler
from app.telegram.identity import telegram_display_name, telegram_entity_id
from app.telegram.ingestion import ingest_telegram_message
from app.telegram.inquiry_backfill import backfill_new_inquiry_messages
from app.telegram.inquiry_events import create_inquiry_message_handlers
from app.telegram.inquiry_ingestion import (
    ingest_inquiry_telegram_message,
    retry_pending_inquiry_media,
)
from app.telegram.peer_ids import normalize_telegram_chat_id
from app.telegram.reaction_matching import parse_completion_reactions

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
        report(
            f"  Cashout group configured: yes (ID: {settings.telegram_cashout_group_id})"
        )
    else:
        report("  Cashout group configured: no")
    allowlist = parse_completion_reactions(settings.cashout_completion_reactions)
    if allowlist is None:
        report("  Cashout completion reactions: any")
    else:
        report(f"  Cashout completion reactions: {', '.join(sorted(allowlist))}")
    report(
        "  Cashout reconciliation interval: "
        f"{settings.cashout_reconciliation_interval_seconds}s"
    )


async def run_listener(report: TerminalReporter = print) -> None:
    """Start the Telegram listener with automatic reconnect and reconciliation."""
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
            report(
                f"Listener session failed; reconnecting in {reconnect_delay}s…"
            )
            await asyncio.sleep(reconnect_delay)
            reconnect_delay = min(RECONNECT_MAX_SECONDS, reconnect_delay * 2)
            continue

        listener_health.mark_restart()
        report(f"Telegram disconnected; reconnecting in {reconnect_delay}s…")
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
    reconciliation_task: asyncio.Task[None] | None = None
    allowed_reactions = settings.cashout_completion_reaction_allowlist

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

        # Startup reconciliation catches reactions applied while offline.
        report("Reconciling cashout reactions…")
        startup_results = await reconcile_pending_cashout_reactions(
            client,
            cashout_group_input,
            expected_chat_id=cashout_group_chat_id,
            allowed_reactions=allowed_reactions,
            limit=settings.cashout_reconciliation_batch_size,
        )
        startup_completed = sum(1 for item in startup_results if item.completed)
        if startup_completed:
            report(f"Startup reconciliation completed {startup_completed} cashout(s).")
        listener_health.mark_reconciliation(error=None)

        report("Retrying pending inquiry media…")
        media_recovered = await retry_pending_inquiry_media(
            client,
            cashout_group_input,
            limit=settings.inquiry_page_size_default,
        )
        if media_recovered:
            report(f"Recovered {media_recovered} inquiry media file(s).")

        reaction_handler = create_reaction_handler(
            expected_chat_id=cashout_group_chat_id,
            allowed_reactions=allowed_reactions,
            complete_recent_reactions=lambda: complete_recent_cashout_reactions(
                client,
                cashout_group_input,
                expected_chat_id=cashout_group_chat_id,
                allowed_reactions=allowed_reactions,
            ),
            report=report,
        )
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
        client.add_event_handler(inquiry_new_handler, events.NewMessage(chats=cashout_group_input))
        client.add_event_handler(
            inquiry_edit_handler,
            events.MessageEdited(chats=cashout_group_input),
        )
        client.add_event_handler(
            inquiry_delete_handler,
            events.MessageDeleted(chats=cashout_group_input),
        )
        client.add_event_handler(reaction_handler, events.Raw())
        report("Listening for message reactions.")
        logger.info(
            "telegram_reaction_listener_subscribed",
            extra={"telegram_group": cashout_group_chat_id},
        )
        delivery_task = asyncio.create_task(
            run_cashout_delivery_worker(
                client,
                cashout_group_input,
                telegram_chat_id=cashout_group_chat_id,
            ),
            name="cashout-delivery",
        )
        reconciliation_task = asyncio.create_task(
            run_cashout_reaction_reconciliation_loop(
                client,
                cashout_group_input,
                expected_chat_id=cashout_group_chat_id,
                allowed_reactions=allowed_reactions,
                interval_seconds=float(
                    settings.cashout_reconciliation_interval_seconds
                ),
                batch_size=settings.cashout_reconciliation_batch_size,
                report=report,
            ),
            name="cashout-reaction-reconciliation",
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
        listener_health.mark_disconnected()
        for task in (delivery_task, reconciliation_task):
            if task is not None:
                task.cancel()
        await asyncio.gather(
            *(task for task in (delivery_task, reconciliation_task) if task is not None),
            return_exceptions=True,
        )
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

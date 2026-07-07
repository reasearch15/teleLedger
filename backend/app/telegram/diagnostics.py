from collections.abc import Callable

from app.schemas.telegram import IncomingTelegramMessage
from app.services.telegram_ingestion import TelegramIngestionResult

TerminalReporter = Callable[[str], None]


def report_ingestion_diagnostic(
    incoming: IncomingTelegramMessage,
    result: TelegramIngestionResult,
    report: TerminalReporter = print,
) -> None:
    """Print one audit-friendly diagnostic block for an ingestion attempt."""
    yes_no = {True: "yes", False: "no"}
    report("------------------------------------------------")
    report("Telegram message:")
    report(f"Message ID: {incoming.telegram_message_id}")
    report(f"Existing raw message: {yes_no[result.existing_raw_message]}")
    report(f"Existing payment_event: {yes_no[result.existing_payment_event]}")
    report(f"Parser matched: {yes_no[result.parser_matched]}")
    report(f"Payment inserted: {yes_no[result.payment_inserted]}")
    report(f"Reason skipped: {result.reason_skipped or 'none'}")
    report("------------------------------------------------")

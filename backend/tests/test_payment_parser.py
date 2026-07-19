from datetime import datetime
from decimal import Decimal

import pytest

from app.parser.payment import UNKNOWN_RECIPIENT_TAG, parse_payment_message

VALID_PAYMENT_MESSAGE = """Hi Stephen_Mckinney_21,

You received $36.28 from Krista R.

03:08 PM - 29 Jun 2026
Total In: 5709.59$
Total Out: 1881.66$"""

LARRY_PAYMENT_MESSAGE = """Hi $Nicole_Yannotti_1,

You received $30.00 from Alex P.

04:15 PM - 07 Jul 2026
➕ Total In: 1630.57$
➖ Total Out: 0.00$"""

NO_GREETING_PAYMENT_MESSAGE = """You received $36.28 from Krista R.

03:08 PM - 29 Jun 2026
Total In: 5709.59$
Total Out: 1881.66$"""

REAL_TELEGRAM_PAYMENT_MESSAGE = """🟢 Hi Stephen_Mckinney_21,

You received $36.28 from Krista R.

03:08 PM - 29 Jun 2026
➕ Total In : 5709.59$
➖ Total Out: 1881.66$"""


def test_valid_payment_message() -> None:
    parsed = parse_payment_message(VALID_PAYMENT_MESSAGE)

    assert parsed is not None
    assert parsed.payment_datetime == datetime(2026, 6, 29, 15, 8)


def test_real_telegram_format_with_status_markers() -> None:
    parsed = parse_payment_message(REAL_TELEGRAM_PAYMENT_MESSAGE)

    assert parsed is not None
    assert parsed.amount == Decimal("36.28")
    assert parsed.total_in == Decimal("5709.59")
    assert parsed.total_out == Decimal("1881.66")


def test_payment_block_with_trailing_commentary() -> None:
    parsed = parse_payment_message(
        f"{REAL_TELEGRAM_PAYMENT_MESSAGE}\n\nOperational note after the notification."
    )

    assert parsed is not None
    assert parsed.payment_sender_name == "Krista R"


def test_normal_chat_is_ignored() -> None:
    assert parse_payment_message("Hi team, are we ready for today's reconciliation?") is None


@pytest.mark.parametrize(
    "message",
    [
        "You received $36.28 from Krista R.",
        VALID_PAYMENT_MESSAGE.replace("03:08 PM", "25:99 PM"),
        VALID_PAYMENT_MESSAGE.replace("$36.28", "$not-a-number"),
    ],
)
def test_malformed_payment_is_ignored(message: str) -> None:
    assert parse_payment_message(message) is None


def test_amount_extraction() -> None:
    parsed = parse_payment_message(VALID_PAYMENT_MESSAGE)

    assert parsed is not None
    assert parsed.amount == Decimal("36.28")


def test_sender_extraction() -> None:
    parsed = parse_payment_message(VALID_PAYMENT_MESSAGE)

    assert parsed is not None
    assert parsed.payment_sender_name == "Krista R"


def test_recipient_tag_extraction() -> None:
    parsed = parse_payment_message(VALID_PAYMENT_MESSAGE)

    assert parsed is not None
    assert parsed.recipient_tag == "Stephen_Mckinney_21"


def test_old_format_with_greeting() -> None:
    parsed = parse_payment_message(VALID_PAYMENT_MESSAGE)

    assert parsed is not None
    assert parsed.recipient_tag == "Stephen_Mckinney_21"
    assert parsed.amount == Decimal("36.28")


def test_larry_format_with_dollar_tag() -> None:
    parsed = parse_payment_message(LARRY_PAYMENT_MESSAGE)

    assert parsed is not None
    assert parsed.recipient_tag == "Nicole_Yannotti_1"
    assert parsed.amount == Decimal("30.00")
    assert parsed.payment_sender_name == "Alex P"
    assert parsed.total_in == Decimal("1630.57")
    assert parsed.total_out == Decimal("0.00")


def test_format_without_greeting() -> None:
    parsed = parse_payment_message(NO_GREETING_PAYMENT_MESSAGE)

    assert parsed is not None
    assert parsed.recipient_tag == UNKNOWN_RECIPIENT_TAG
    assert parsed.amount == Decimal("36.28")
    assert parsed.payment_sender_name == "Krista R"


def test_malformed_message_without_you_received_returns_none() -> None:
    message = """Hi Stephen_Mckinney_21,

03:08 PM - 29 Jun 2026
Total In: 5709.59$
Total Out: 1881.66$"""

    assert parse_payment_message(message) is None


@pytest.mark.parametrize(
    "amount",
    ["$30", "$30.0", "$30.00"],
)
def test_amount_formats(amount: str) -> None:
    message = NO_GREETING_PAYMENT_MESSAGE.replace("$36.28", amount)
    parsed = parse_payment_message(message)

    assert parsed is not None
    assert parsed.amount == Decimal("30.00")


def test_total_in_and_out_extraction() -> None:
    parsed = parse_payment_message(VALID_PAYMENT_MESSAGE)

    assert parsed is not None
    assert parsed.total_in == Decimal("5709.59")
    assert parsed.total_out == Decimal("1881.66")


def test_old_format_without_totals_still_parses_required_fields() -> None:
    message = """Hi Stephen_Mckinney_21,

You received $36.28 from Krista R.

03:08 PM - 29 Jun 2026"""

    parsed = parse_payment_message(message)

    assert parsed is not None
    assert parsed.recipient_tag == "Stephen_Mckinney_21"
    assert parsed.amount == Decimal("36.28")
    assert parsed.payment_sender_name == "Krista R"
    assert parsed.payment_datetime == datetime(2026, 6, 29, 15, 8)
    assert parsed.total_in is None
    assert parsed.total_out is None


def test_informational_lines_between_required_fields_and_totals_are_ignored() -> None:
    message = """🟢 Hi $Demaul_Goins,

You received $10.0 from Emily S.

08:09 AM - 18 Jul 2026
Reference: provider-generated metadata
➕ Total In : 517.7$
➖ Total Out: 0$"""

    parsed = parse_payment_message(message)

    assert parsed is not None
    assert parsed.recipient_tag == "Demaul_Goins"
    assert parsed.amount == Decimal("10.0")
    assert parsed.total_in == Decimal("517.7")
    assert parsed.total_out == Decimal("0")


def test_total_lines_accept_leading_or_trailing_currency_symbol() -> None:
    message = """🟢 Hi $Demaul_Goins,

You received $10.0 from Emily S.

08:09 AM - 18 Jul 2026
➕ Total In : $517.7
➖ Total Out: $0"""

    parsed = parse_payment_message(message)

    assert parsed is not None
    assert parsed.total_in == Decimal("517.7")
    assert parsed.total_out == Decimal("0")

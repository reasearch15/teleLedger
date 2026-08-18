from datetime import datetime
from decimal import Decimal

import pytest

from app.parser.payment import (
    CHIME_PAYMENT_METHOD,
    UNKNOWN_RECIPIENT_TAG,
    parse_payment_message,
)

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


def _chime_notification(
    *,
    amount: str,
    name: str,
    received_at: str = "18 Aug 2026, 6:17 AM",
    header: str = "",
    title: str = "🟢 New Chime Payment",
    amount_label: str = "💵 Amount Received",
    name_label: str = "👤 Payment Name",
    received_label: str = "🕘 Received At",
) -> str:
    prefix = f"{header.strip()}\n\n" if header.strip() else ""
    return (
        f"{prefix}{title}\n"
        f"\n"
        f"{amount_label}: {amount}\n"
        f"{name_label}: {name}\n"
        f"{received_label}: {received_at}\n"
    )


@pytest.mark.parametrize(
    ("amount", "name", "expected_amount"),
    [
        ("$5.00", "mariah f.", Decimal("5.00")),
        ("$10.00", "Calvin M.", Decimal("10.00")),
        ("$20.00", "Calvin M.", Decimal("20.00")),
        ("$16.50", "Calvin M.", Decimal("16.50")),
        ("$10.72", "Edward M.", Decimal("10.72")),
        ("$15.00", "Cayce P.", Decimal("15.00")),
        ("$4.98", "Kayla W.", Decimal("4.98")),
    ],
)
def test_new_chime_notification_amounts_and_names(
    amount: str,
    name: str,
    expected_amount: Decimal,
) -> None:
    parsed = parse_payment_message(_chime_notification(amount=amount, name=name))

    assert parsed is not None
    assert parsed.amount == expected_amount
    assert parsed.payment_sender_name == name
    assert parsed.payment_method == CHIME_PAYMENT_METHOD
    assert parsed.recipient_tag == UNKNOWN_RECIPIENT_TAG
    assert parsed.payment_datetime == datetime(2026, 8, 18, 6, 17)
    assert parsed.total_in is None
    assert parsed.total_out is None


def test_new_chime_notification_with_lulla_header() -> None:
    parsed = parse_payment_message(
        _chime_notification(
            amount="$5.00",
            name="Calvin M.",
            header="Lulla Cash In",
        )
    )

    assert parsed is not None
    assert parsed.amount == Decimal("5.00")
    assert parsed.payment_sender_name == "Calvin M."
    assert parsed.payment_method == CHIME_PAYMENT_METHOD


def test_new_chime_notification_whitespace_and_missing_emoji() -> None:
    message = """
    Lulla Cash In

    New Chime Payment

    Amount   Received :  $16.50
    Payment  Name:   Calvin M.
    Received  At : 18 Aug 2026, 6:40 AM
    """

    parsed = parse_payment_message(message)

    assert parsed is not None
    assert parsed.amount == Decimal("16.50")
    assert parsed.payment_sender_name == "Calvin M."
    assert parsed.payment_datetime == datetime(2026, 8, 18, 6, 40)


def test_new_chime_notification_clock_emoji_variants() -> None:
    parsed = parse_payment_message(
        _chime_notification(
            amount="$10.72",
            name="Edward M.",
            received_at="18 Aug 2026, 7:17 AM",
            received_label="🕒 Received At",
        )
    )

    assert parsed is not None
    assert parsed.amount == Decimal("10.72")
    assert parsed.payment_datetime == datetime(2026, 8, 18, 7, 17)


@pytest.mark.parametrize(
    "message",
    [
        _chime_notification(amount="$not-a-number", name="Calvin M."),
        _chime_notification(amount="$10.999", name="Calvin M."),
        _chime_notification(amount="$", name="Calvin M."),
        """🟢 New Chime Payment

💵 Amount Received: $5.00
🕘 Received At: 18 Aug 2026, 6:17 AM
""",
        """🟢 New Chime Payment

💵 Amount Received: $5.00
👤 Payment Name:
🕘 Received At: 18 Aug 2026, 6:17 AM
""",
        """🟢 New Chime Payment

👤 Payment Name: Calvin M.
🕘 Received At: 18 Aug 2026, 6:17 AM
""",
        "Hi team, are we ready for today's reconciliation?",
        "Lulla Cash In posted in the cash-in group.",
        "Amount Received: $5.00 from someone in chat.",
    ],
)
def test_new_chime_notification_rejects_incomplete_or_unrelated(message: str) -> None:
    assert parse_payment_message(message) is None

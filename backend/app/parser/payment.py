from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from app.core.logging import get_logger
from app.schemas.payment import ParsedPayment

UNKNOWN_RECIPIENT_TAG = "unknown"
CHIME_PAYMENT_METHOD = "Chime"

logger = get_logger(__name__)

_MONEY_PATTERN = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"
# Real notifications include presentation symbols that are absent from the
# provider's plain-text sample. Keep these optional and line-scoped.
_OPTIONAL_MARKER = r"(?:[^\w\s$]{1,4}[ \t]*)?"
_GREETING_PATTERN = re.compile(
    rf"^[ \t]*(?:{_OPTIONAL_MARKER})?Hi[ \t]+\$?"
    rf"(?P<recipient_tag>[A-Za-z0-9_]+),[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_PAYMENT_LINE_PATTERN = re.compile(
    rf"^[ \t]*(?:{_OPTIONAL_MARKER})?You[ \t]+received[ \t]+"
    rf"\$?(?P<amount>{_MONEY_PATTERN})[ \t]+from[ \t]+"
    rf"(?P<payment_sender_name>[^\r\n]+?)\.[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_TIMESTAMP_PATTERN = re.compile(
    r"""
    ^[ \t]*(?P<hour>\d{1,2}):(?P<minute>\d{2})[ \t]+(?P<meridiem>AM|PM)
    [ \t]+-[ \t]+(?P<day>\d{1,2})[ \t]+(?P<month>[A-Za-z]{3})
    [ \t]+(?P<year>\d{4})[ \t]*$
    """,
    re.IGNORECASE | re.MULTILINE | re.VERBOSE,
)
_TOTAL_PATTERN = re.compile(
    rf"^[ \t]*(?:{_OPTIONAL_MARKER})?Total[ \t]+(?P<kind>In|Out)"
    rf"[ \t]*:[ \t]*\$?(?P<value>{_MONEY_PATTERN})\$?[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_CHIME_TITLE_PATTERN = re.compile(
    rf"^[ \t]*(?:{_OPTIONAL_MARKER})?New[ \t]+Chime[ \t]+Payment[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_CHIME_AMOUNT_PATTERN = re.compile(
    rf"^[ \t]*(?:{_OPTIONAL_MARKER})?Amount[ \t]+Received[ \t]*:[ \t]*"
    rf"\$(?P<amount>{_MONEY_PATTERN})[ \t]*$",
    re.IGNORECASE | re.MULTILINE,
)
_CHIME_NAME_PATTERN = re.compile(
    rf"^[ \t]*(?:{_OPTIONAL_MARKER})?Payment[ \t]+Name[ \t]*:[ \t]*"
    rf"(?P<name>\S[^\r\n]*?)\s*$",
    re.IGNORECASE | re.MULTILINE,
)
_CHIME_RECEIVED_AT_PATTERN = re.compile(
    rf"""
    ^[ \t]*(?:{_OPTIONAL_MARKER})?Received[ \t]+At[ \t]*:[ \t]*
    (?P<day>\d{{1,2}})[ \t]+(?P<month>[A-Za-z]{{3}})[ \t]+(?P<year>\d{{4}})
    [ \t]*,[ \t]*(?P<hour>\d{{1,2}}):(?P<minute>\d{{2}})[ \t]+(?P<meridiem>AM|PM)
    [ \t]*$
    """,
    re.IGNORECASE | re.MULTILINE | re.VERBOSE,
)

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}


def _parse_decimal(value: str) -> Decimal:
    return Decimal(value.replace(",", ""))


def _parse_datetime(match: re.Match[str]) -> datetime:
    hour = int(match.group("hour"))
    minute = int(match.group("minute"))
    meridiem = match.group("meridiem").upper()

    if not 1 <= hour <= 12 or not 0 <= minute <= 59:
        raise ValueError("Invalid payment time")

    hour = hour % 12
    if meridiem == "PM":
        hour += 12

    month_name = match.group("month").lower()
    try:
        month = _MONTHS[month_name]
    except KeyError as error:
        raise ValueError("Invalid payment month") from error

    return datetime(
        year=int(match.group("year")),
        month=month,
        day=int(match.group("day")),
        hour=hour,
        minute=minute,
    )


def _build_parsed_payment(
    *,
    recipient_tag: str,
    amount: Decimal,
    payment_sender_name: str,
    payment_datetime: datetime,
    payment_method: str = CHIME_PAYMENT_METHOD,
    total_in: Decimal | None = None,
    total_out: Decimal | None = None,
) -> ParsedPayment | None:
    try:
        parsed = ParsedPayment(
            recipient_tag=recipient_tag,
            amount=amount,
            payment_sender_name=payment_sender_name,
            payment_datetime=payment_datetime,
            payment_method=payment_method,
            total_in=total_in,
            total_out=total_out,
        )
    except (InvalidOperation, ValidationError, TypeError, ValueError) as error:
        logger.debug(
            "payment_parser_validation_failed",
            extra={"error": str(error)},
        )
        return None
    logger.debug(
        "payment_parser_validation_succeeded",
        extra={
            "recipient_tag": parsed.recipient_tag,
            "amount": str(parsed.amount),
            "payment_sender_name": parsed.payment_sender_name,
            "payment_datetime": parsed.payment_datetime.isoformat(),
            "payment_method": parsed.payment_method,
            "total_in": str(parsed.total_in) if parsed.total_in is not None else None,
            "total_out": str(parsed.total_out) if parsed.total_out is not None else None,
        },
    )
    return parsed


def _parse_chime_notification(body: str) -> ParsedPayment | None:
    """Parse structured Lulla/Payment Telebot Chime notifications."""
    title_match = _CHIME_TITLE_PATTERN.search(body)
    amount_match = _CHIME_AMOUNT_PATTERN.search(body)
    name_match = _CHIME_NAME_PATTERN.search(body)
    received_at_match = _CHIME_RECEIVED_AT_PATTERN.search(body)
    logger.debug(
        "chime_notification_regex_matches",
        extra={
            "has_title": title_match is not None,
            "has_amount": amount_match is not None,
            "has_payment_name": name_match is not None,
            "has_received_at": received_at_match is not None,
        },
    )
    if title_match is None:
        return None
    if amount_match is None or name_match is None or received_at_match is None:
        logger.debug(
            "chime_notification_required_fields_missing",
            extra={
                "has_amount": amount_match is not None,
                "has_payment_name": name_match is not None,
                "has_received_at": received_at_match is not None,
            },
        )
        return None

    try:
        amount = _parse_decimal(amount_match.group("amount"))
        payment_datetime = _parse_datetime(received_at_match)
    except (InvalidOperation, ValueError) as error:
        logger.debug(
            "chime_notification_field_invalid",
            extra={"error": str(error)},
        )
        return None

    return _build_parsed_payment(
        recipient_tag=UNKNOWN_RECIPIENT_TAG,
        amount=amount,
        payment_sender_name=name_match.group("name").strip(),
        payment_datetime=payment_datetime,
        payment_method=CHIME_PAYMENT_METHOD,
    )


def _parse_legacy_payment_message(body: str) -> ParsedPayment | None:
    """Parse the original PICCASO / Larry-style payment notification."""
    greeting_match = _GREETING_PATTERN.search(body)
    payment_match = _PAYMENT_LINE_PATTERN.search(body)
    timestamp_match = _TIMESTAMP_PATTERN.search(body)
    total_matches = list(_TOTAL_PATTERN.finditer(body))
    logger.debug(
        "payment_parser_regex_matches",
        extra={
            "has_greeting": greeting_match is not None,
            "has_payment_line": payment_match is not None,
            "has_timestamp": timestamp_match is not None,
            "total_match_count": len(total_matches),
        },
    )
    if payment_match is None or timestamp_match is None:
        logger.debug(
            "payment_parser_required_fields_missing",
            extra={
                "has_payment_line": payment_match is not None,
                "has_timestamp": timestamp_match is not None,
            },
        )
        return None

    totals: dict[str, Decimal] = {}
    for total_match in total_matches:
        kind = total_match.group("kind").lower()
        try:
            totals[kind] = _parse_decimal(total_match.group("value"))
        except InvalidOperation:
            logger.debug(
                "payment_parser_total_invalid",
                extra={"total_kind": kind, "total_value": total_match.group("value")},
            )

    recipient_tag = (
        greeting_match.group("recipient_tag") if greeting_match else UNKNOWN_RECIPIENT_TAG
    )
    try:
        amount = _parse_decimal(payment_match.group("amount"))
        payment_datetime = _parse_datetime(timestamp_match)
    except (InvalidOperation, ValueError) as error:
        logger.debug(
            "payment_parser_legacy_field_invalid",
            extra={"error": str(error)},
        )
        return None

    return _build_parsed_payment(
        recipient_tag=recipient_tag,
        amount=amount,
        payment_sender_name=payment_match.group("payment_sender_name").strip(),
        payment_datetime=payment_datetime,
        payment_method=CHIME_PAYMENT_METHOD,
        total_in=totals.get("in"),
        total_out=totals.get("out"),
    )


def parse_payment_message(raw_text: str) -> ParsedPayment | None:
    """Parse a complete payment notification, returning None for all other input."""
    body = raw_text.strip()
    parsed = _parse_chime_notification(body)
    if parsed is not None:
        return parsed
    return _parse_legacy_payment_message(body)

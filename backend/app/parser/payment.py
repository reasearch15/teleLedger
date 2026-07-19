from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from app.core.logging import get_logger
from app.schemas.payment import ParsedPayment

UNKNOWN_RECIPIENT_TAG = "unknown"

logger = get_logger(__name__)

_MONEY_PATTERN = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"
# Real notifications include presentation symbols that are absent from the
# provider's plain-text sample. Keep these optional and line-scoped.
_OPTIONAL_MARKER = r"(?:[^\w\s$]{1,3}[ \t]*)?"
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


def parse_payment_message(raw_text: str) -> ParsedPayment | None:
    """Parse a complete payment notification, returning None for all other input."""
    body = raw_text.strip()
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
            "raw_text": raw_text,
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
        parsed = ParsedPayment(
            recipient_tag=recipient_tag,
            amount=_parse_decimal(payment_match.group("amount")),
            payment_sender_name=payment_match.group("payment_sender_name").strip(),
            payment_datetime=_parse_datetime(timestamp_match),
            total_in=totals.get("in"),
            total_out=totals.get("out"),
        )
    except (InvalidOperation, ValidationError, ValueError) as error:
        logger.debug(
            "payment_parser_validation_failed",
            extra={"error": str(error), "raw_text": raw_text},
        )
        return None
    logger.debug(
        "payment_parser_validation_succeeded",
        extra={
            "recipient_tag": parsed.recipient_tag,
            "amount": str(parsed.amount),
            "payment_sender_name": parsed.payment_sender_name,
            "payment_datetime": parsed.payment_datetime.isoformat(),
            "total_in": str(parsed.total_in) if parsed.total_in is not None else None,
            "total_out": str(parsed.total_out) if parsed.total_out is not None else None,
        },
    )
    return parsed

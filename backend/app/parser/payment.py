from __future__ import annotations

import re
from datetime import datetime
from decimal import Decimal, InvalidOperation

from pydantic import ValidationError

from app.schemas.payment import ParsedPayment

UNKNOWN_RECIPIENT_TAG = "unknown"

_MONEY_PATTERN = r"(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d{1,2})?"
# Real notifications include presentation symbols that are absent from the
# provider's plain-text sample. Keep these optional and line-scoped.
_OPTIONAL_MARKER = r"(?:[^\w\s$]{1,3}[ \t]*)?"
_OPTIONAL_GREETING = (
    rf"[ \t]*(?:{_OPTIONAL_MARKER})?Hi[ \t]+\$?"
    rf"(?P<recipient_tag>[A-Za-z0-9_]+),[ \t]*\r?\n"
    rf"[ \t]*\r?\n"
)
_PAYMENT_MESSAGE_PATTERN = re.compile(
    rf"""
    \A
    (?:{_OPTIONAL_GREETING})?
    [ \t]*You[ \t]+received[ \t]+\$(?P<amount>{_MONEY_PATTERN})
    [ \t]+from[ \t]+(?P<payment_sender_name>[^\r\n]+?)\.[ \t]*\r?\n
    [ \t]*\r?\n
    [ \t]*(?P<hour>\d{{1,2}}):(?P<minute>\d{{2}})[ \t]+(?P<meridiem>AM|PM)
    [ \t]+-[ \t]+(?P<day>\d{{1,2}})[ \t]+(?P<month>[A-Za-z]{{3}})
    [ \t]+(?P<year>\d{{4}})[ \t]*\r?\n
    [ \t]*(?:{_OPTIONAL_MARKER})?Total[ \t]+In[ \t]*:[ \t]*
    (?P<total_in>{_MONEY_PATTERN})\$[ \t]*\r?\n
    [ \t]*(?:{_OPTIONAL_MARKER})?Total[ \t]+Out[ \t]*:[ \t]*
    (?P<total_out>{_MONEY_PATTERN})\$[ \t]*
    (?:\r?\n[\s\S]*)?
    \Z
    """,
    re.IGNORECASE | re.VERBOSE,
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
    match = _PAYMENT_MESSAGE_PATTERN.fullmatch(raw_text.strip())
    if match is None:
        return None

    recipient_tag = match.group("recipient_tag") or UNKNOWN_RECIPIENT_TAG

    try:
        return ParsedPayment(
            recipient_tag=recipient_tag,
            amount=_parse_decimal(match.group("amount")),
            payment_sender_name=match.group("payment_sender_name").strip(),
            payment_datetime=_parse_datetime(match),
            total_in=_parse_decimal(match.group("total_in")),
            total_out=_parse_decimal(match.group("total_out")),
        )
    except (InvalidOperation, ValidationError, ValueError):
        return None

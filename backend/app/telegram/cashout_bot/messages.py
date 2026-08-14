from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from app.models.cashout import CashoutCompletionType, CashoutStatus


class CashoutCallbackAction(StrEnum):
    """Compact callback action identifiers stored in Telegram button data."""

    FULL = "full"
    PARTIAL = "partial"


@dataclass(frozen=True, slots=True)
class CashoutTaskView:
    """Renderable cashout task state for Telegram messages."""

    cashout_id: int
    request_number: str
    player_tag: str
    requested_amount: Decimal
    status: CashoutStatus
    requested_by: str
    created_at: datetime
    notes: str | None = None
    completion_type: CashoutCompletionType | None = None
    actual_paid_amount: Decimal | None = None
    completed_by_label: str | None = None
    completed_at: datetime | None = None


def encode_callback_data(cashout_id: int, action: CashoutCallbackAction) -> str:
    """Encode only a cashout reference; financial values stay server-side."""
    return f"c:{cashout_id}:{action.value}"


def decode_callback_data(data: str) -> tuple[int, CashoutCallbackAction] | None:
    """Parse callback data when it matches the expected compact format."""
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "c":
        return None
    try:
        cashout_id = int(parts[1])
        action = CashoutCallbackAction(parts[2])
    except (TypeError, ValueError):
        return None
    return cashout_id, action


def build_active_task_markup(cashout_id: int) -> list[list[tuple[str, str]]]:
    """Return inline button rows as (label, callback_data) tuples."""
    return [
        [
            ("Full Payment", encode_callback_data(cashout_id, CashoutCallbackAction.FULL)),
            (
                "Partial Payment",
                encode_callback_data(cashout_id, CashoutCallbackAction.PARTIAL),
            ),
        ]
    ]


def format_active_cashout_message(view: CashoutTaskView) -> str:
    lines = [
        "CASHOUT REQUEST",
        "",
        f"Request ID: {view.request_number}",
        f"Status: {view.status.value.title()}",
        "",
        "Tag:",
        view.player_tag,
        "",
        "Requested Amount:",
        f"${view.requested_amount:,.2f}",
        "",
        "Requested By:",
        view.requested_by,
        "",
        "Time:",
        view.created_at.strftime("%Y-%m-%d %H:%M UTC"),
    ]
    if view.notes:
        lines.extend(["", "Optional Notes:", view.notes])
    return "\n".join(lines)


def format_completed_cashout_message(view: CashoutTaskView) -> str:
    paid_amount = view.actual_paid_amount or view.requested_amount
    unpaid = view.requested_amount - paid_amount
    if view.completion_type == CashoutCompletionType.PARTIAL:
        lines = [
            "⚠️ CASHOUT PARTIALLY PAID ⚠️",
            "🟡 PARTIAL PAYMENT",
            "",
            f"Request ID: {view.request_number}",
            f"Tag: {view.player_tag}",
            "",
            f"Requested Amount: ${view.requested_amount:,.2f}",
            f"Paid Amount: ${paid_amount:,.2f}",
            f"Remaining Amount: ${unpaid:,.2f}",
            "",
            f"⚠️ ${unpaid:,.2f} STILL UNPAID",
        ]
    else:
        lines = [
            "✅✅ CASHOUT COMPLETED ✅✅",
            "🟢 PAID IN FULL",
            "",
            f"Request ID: {view.request_number}",
            f"Tag: {view.player_tag}",
            "",
            f"Requested Amount: ${view.requested_amount:,.2f}",
            f"Paid Amount: ${paid_amount:,.2f}",
            "",
            "✅ NO BALANCE REMAINING",
        ]
    if view.completed_by_label:
        lines.extend(["", f"Completed By: {view.completed_by_label}"])
    if view.completed_at is not None:
        lines.append(f"Completed At: {view.completed_at.strftime('%Y-%m-%d %H:%M UTC')}")
    return "\n".join(lines)


def format_cancelled_cashout_message(view: CashoutTaskView) -> str:
    lines = [
        "CASHOUT CANCELLED",
        "",
        f"Request ID: {view.request_number}",
        "Status: Cancelled",
        "",
        "Tag:",
        view.player_tag,
        "",
        "Requested Amount:",
        f"${view.requested_amount:,.2f}",
    ]
    return "\n".join(lines)


def format_partial_prompt_message(request_number: str) -> str:
    return (
        f"Enter the amount actually paid for Cashout {request_number}.\n"
        "Reply with a numeric amount, or send cancel to abort."
    )

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
    cancelled_by_label: str | None = None
    cancelled_at: datetime | None = None


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


def format_cashout_task_card(view: CashoutTaskView) -> str:
    """Render a cashout Telegram card from persisted state only."""
    if view.status == CashoutStatus.COMPLETED:
        return format_completed_cashout_message(view)
    if view.status == CashoutStatus.CANCELLED:
        return format_cancelled_cashout_message(view)
    return format_active_cashout_message(view)


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
    ]
    if view.requested_by:
        lines.extend(["", "Requested By:", view.requested_by])
    lines.extend(
        [
            "",
            "Time:",
            view.created_at.strftime("%Y-%m-%d %H:%M UTC"),
        ]
    )
    _append_notes(lines, view.notes)
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
    _append_cashout_actor_lines(
        lines,
        requested_by=view.requested_by,
        completed_by=view.completed_by_label,
    )
    if view.completed_at is not None:
        lines.append(f"Completed At: {view.completed_at.strftime('%Y-%m-%d %H:%M UTC')}")
    _append_notes(lines, view.notes)
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
    _append_cashout_actor_lines(
        lines,
        requested_by=view.requested_by,
        cancelled_by=view.cancelled_by_label,
    )
    if view.cancelled_at is not None:
        lines.append(f"Cancelled At: {view.cancelled_at.strftime('%Y-%m-%d %H:%M UTC')}")
    _append_notes(lines, view.notes)
    return "\n".join(lines)


def format_partial_prompt_message(request_number: str) -> str:
    return (
        f"Enter the amount actually paid for Cashout {request_number}.\n"
        "Reply with a numeric amount, or send cancel to abort."
    )


def format_qr_cashout_caption(view: CashoutTaskView) -> str:
    """Render a QR cash-out Telegram photo caption from persisted state."""
    if view.status == CashoutStatus.COMPLETED:
        return _format_qr_completed_caption(view)
    if view.status == CashoutStatus.CANCELLED:
        return _format_qr_cancelled_caption(view)
    return _format_qr_active_caption(view)


def _format_qr_active_caption(view: CashoutTaskView) -> str:
    return "\n".join(
        [
            f"💸 Cash Out — {view.request_number}",
            f"Amount: ${view.requested_amount:,.2f}",
        ]
    )


def _format_qr_completed_caption(view: CashoutTaskView) -> str:
    paid_amount = view.actual_paid_amount or view.requested_amount
    unpaid = view.requested_amount - paid_amount
    lines = [
        f"💸 Cash Out — {view.request_number}",
        f"Amount: ${view.requested_amount:,.2f}",
    ]
    if view.completion_type == CashoutCompletionType.PARTIAL:
        lines.extend(
            [
                f"Paid: ${paid_amount:,.2f}",
                f"Remaining: ${unpaid:,.2f}",
                "🟡 Partial Payment",
            ]
        )
    else:
        lines.extend([f"Paid: ${paid_amount:,.2f}", "✅ Paid in Full"])
    if view.completed_by_label:
        lines.append(f"Completed By: {view.completed_by_label}")
    if view.completed_at is not None:
        lines.append(f"Completed At: {view.completed_at.strftime('%Y-%m-%d %H:%M UTC')}")
    return "\n".join(lines)


def _format_qr_cancelled_caption(view: CashoutTaskView) -> str:
    lines = [
        f"💸 Cash Out — {view.request_number}",
        f"Amount: ${view.requested_amount:,.2f}",
        "Status: Cancelled",
    ]
    if view.cancelled_by_label:
        lines.append(f"Cancelled By: {view.cancelled_by_label}")
    if view.cancelled_at is not None:
        lines.append(f"Cancelled At: {view.cancelled_at.strftime('%Y-%m-%d %H:%M UTC')}")
    return "\n".join(lines)


def _append_cashout_actor_lines(
    lines: list[str],
    *,
    requested_by: str | None = None,
    completed_by: str | None = None,
    cancelled_by: str | None = None,
) -> None:
    extras: list[str] = []
    if requested_by:
        extras.append(f"Requested By: {requested_by}")
    if completed_by:
        extras.append(f"Completed By: {completed_by}")
    if cancelled_by:
        extras.append(f"Cancelled By: {cancelled_by}")
    if extras:
        lines.extend(["", *extras])


def _append_notes(lines: list[str], notes: str | None) -> None:
    text = notes.strip() if notes else ""
    if not text:
        return
    lines.extend(["", "Optional Notes:", text])

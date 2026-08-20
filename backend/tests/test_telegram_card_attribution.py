from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

from app.models.cashout import CashoutCompletionType, CashoutStatus
from app.telegram.cashout_bot.messages import (
    CashoutTaskView,
    format_cashout_task_card,
)
from app.telegram.staff_labels import format_actor_label
from app.telegram.venmo_confirmation import (
    TELEGRAM_CAPTION_MAX_LENGTH,
    VENMO_NOTE_OVERFLOW_MARKER,
    VenmoConfirmationCardView,
    apply_telegram_caption_limit,
    format_venmo_confirmation_card,
)


CREATED_AT = datetime(2026, 7, 6, 20, 35, tzinfo=UTC)


def cashout_view(**overrides: object) -> CashoutTaskView:
    values: dict[str, object] = {
        "cashout_id": 1,
        "request_number": "CR-000001",
        "player_tag": "john123",
        "requested_amount": Decimal("25.00"),
        "status": CashoutStatus.SENT,
        "requested_by": "Bella",
        "created_at": CREATED_AT,
        "notes": "Player asked for partial payment first.",
    }
    values.update(overrides)
    return CashoutTaskView(**values)  # type: ignore[arg-type]


def test_initial_cashout_card_includes_creator_and_note() -> None:
    text = format_cashout_task_card(cashout_view())
    assert "Requested By:\nBella" in text
    assert "Optional Notes:\nPlayer asked for partial payment first." in text
    assert text.count("Optional Notes:") == 1


def test_full_payment_edit_preserves_note_and_creator() -> None:
    text = format_cashout_task_card(
        cashout_view(
            status=CashoutStatus.COMPLETED,
            completion_type=CashoutCompletionType.FULL,
            actual_paid_amount=Decimal("25.00"),
            completed_by_label="Charlie",
            completed_at=datetime(2026, 7, 6, 21, 0, tzinfo=UTC),
        )
    )
    assert "Requested By: Bella" in text
    assert "Completed By: Charlie" in text
    assert "Optional Notes:\nPlayer asked for partial payment first." in text


def test_partial_edit_preserves_note() -> None:
    text = format_cashout_task_card(
        cashout_view(
            status=CashoutStatus.COMPLETED,
            completion_type=CashoutCompletionType.PARTIAL,
            actual_paid_amount=Decimal("10.00"),
            completed_by_label="Bella",
        )
    )
    assert "🟡 PARTIAL PAYMENT" in text
    assert "Optional Notes:\nPlayer asked for partial payment first." in text
    assert "Requested By: Bella" in text


def test_cancel_edit_preserves_note() -> None:
    text = format_cashout_task_card(
        cashout_view(
            status=CashoutStatus.CANCELLED,
            cancelled_by_label="Ayush",
            cancelled_at=datetime(2026, 7, 6, 21, 5, tzinfo=UTC),
        )
    )
    assert "CASHOUT CANCELLED" in text
    assert "Requested By: Bella" in text
    assert "Cancelled By: Ayush" in text
    assert "Optional Notes:\nPlayer asked for partial payment first." in text


def test_repeated_cashout_renders_do_not_duplicate_note() -> None:
    view = cashout_view(status=CashoutStatus.COMPLETED, completed_by_label="Charlie")
    first = format_cashout_task_card(view)
    second = format_cashout_task_card(view)
    assert first == second
    assert first.count("Optional Notes:") == 1
    assert first.count("Player asked for partial payment first.") == 1


def test_venmo_initial_card_preserves_note_and_creator() -> None:
    caption = format_venmo_confirmation_card(
        VenmoConfirmationCardView(
            request_id=12,
            attempt_number=1,
            status="pending",
            note="Payment name differs from account name.",
            requested_by="Ayush",
        )
    ).caption
    assert caption.startswith("Confirmation request #12\nAttempt #1")
    assert "Requested By: Ayush" in caption
    assert "Note: Payment name differs from account name." in caption
    assert "Was this evidence received/accepted?" in caption


def test_venmo_not_received_and_confirm_preserve_note() -> None:
    note = "Payment name differs from account name."
    not_received = format_venmo_confirmation_card(
        VenmoConfirmationCardView(
            request_id=12,
            status="not_received",
            note=note,
            requested_by="Ayush",
            not_received_by="Bella",
        )
    ).caption
    confirmed = format_venmo_confirmation_card(
        VenmoConfirmationCardView(
            request_id=12,
            status="confirmed",
            note=note,
            requested_by="Ayush",
            confirmed_by="Ayush",
            confirmed_at=datetime(2026, 7, 15, 16, 0, tzinfo=UTC),
        )
    ).caption
    assert "Note: Payment name differs from account name." in not_received
    assert "Not Received By: Bella" in not_received
    assert "Requested By: Ayush" in not_received
    assert "Note: Payment name differs from account name." in confirmed
    assert "Confirmed By: Ayush" in confirmed
    assert "Requested By: Ayush" in confirmed
    assert confirmed.count("Note:") == 1


def test_venmo_send_again_pending_card_keeps_note() -> None:
    caption = format_venmo_confirmation_card(
        VenmoConfirmationCardView(
            request_id=12,
            attempt_number=2,
            status="pending",
            note="Payment name differs from account name.",
            requested_by="Ayush",
        )
    ).caption
    assert "Attempt #2" in caption
    assert "Note: Payment name differs from account name." in caption


def test_actor_display_survives_rerender() -> None:
    view = VenmoConfirmationCardView(
        request_id=12,
        status="confirmed",
        note="Keep me",
        requested_by="Ayush",
        confirmed_by="Ayush",
    )
    first = format_venmo_confirmation_card(view).caption
    second = format_venmo_confirmation_card(view).caption
    assert first == second
    assert "Confirmed By: Ayush" in second
    assert "Note: Keep me" in second


def test_missing_display_name_falls_back_safely() -> None:
    assert format_actor_label(username="sarah") == "sarah"
    assert format_actor_label(telegram_username="operator") == "@operator"
    assert format_actor_label(telegram_user_id=9001) == "Telegram user 9001"
    assert format_actor_label() is None
    assert "Unknown" not in (format_actor_label(telegram_user_id=9001) or "")


def test_caption_overflow_does_not_truncate_note() -> None:
    note = "N" * (TELEGRAM_CAPTION_MAX_LENGTH + 50)
    result = apply_telegram_caption_limit(
        "Confirmation request #12\nAttempt #1\nWas this evidence received/accepted?",
        note=note,
    )
    assert note not in result.caption
    assert VENMO_NOTE_OVERFLOW_MARKER in result.caption
    assert result.overflow_text == f"Note: {note}"
    assert note in (result.overflow_text or "")
    assert len(result.caption) <= TELEGRAM_CAPTION_MAX_LENGTH

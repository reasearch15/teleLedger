from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

TELEGRAM_CAPTION_MAX_LENGTH = 1024
VENMO_NOTE_OVERFLOW_MARKER = "Note stored on this request (too long for Telegram caption)."


class VenmoConfirmationCallbackAction(StrEnum):
    CONFIRM = "confirm"
    NOT_RECEIVED = "not_received"


@dataclass(frozen=True, slots=True)
class VenmoConfirmationCardView:
    """Renderable Venmo confirmation state sourced from persisted records."""

    request_id: int
    attempt_number: int | None = None
    status: str | None = None
    note: str | None = None
    requested_by: str | None = None
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None
    not_received_by: str | None = None


@dataclass(frozen=True, slots=True)
class VenmoConfirmationCaption:
    caption: str
    overflow_text: str | None = None


def encode_venmo_confirmation_callback(
    attempt_id: int,
    action: VenmoConfirmationCallbackAction,
) -> str:
    return f"vc:{attempt_id}:{action.value}"


def decode_venmo_confirmation_callback(
    data: str,
) -> tuple[int, VenmoConfirmationCallbackAction] | None:
    parts = data.split(":")
    if len(parts) != 3 or parts[0] != "vc":
        return None
    try:
        attempt_id = int(parts[1])
        action = VenmoConfirmationCallbackAction(parts[2])
    except (ValueError, TypeError):
        return None
    if attempt_id <= 0:
        return None
    return attempt_id, action


def venmo_confirmation_buttons(attempt_id: int) -> list[list[tuple[str, str]]]:
    return [
        [
            (
                "Confirm",
                encode_venmo_confirmation_callback(
                    attempt_id,
                    VenmoConfirmationCallbackAction.CONFIRM,
                ),
            ),
            (
                "Not Received",
                encode_venmo_confirmation_callback(
                    attempt_id,
                    VenmoConfirmationCallbackAction.NOT_RECEIVED,
                ),
            ),
        ]
    ]


def format_venmo_confirmation_card(
    view: VenmoConfirmationCardView,
) -> VenmoConfirmationCaption:
    """Render a Venmo confirmation caption from persisted request state."""
    status = (view.status or "pending").casefold()
    if status == "confirmed":
        body = _confirmed_body(view)
    elif status == "not_received":
        body = _not_received_body(view)
    else:
        body = _pending_body(view)
    return apply_telegram_caption_limit(body, note=_canonical_note(view.note))


def venmo_confirmation_caption(
    *,
    request_id: int,
    attempt_number: int,
    note: str | None,
    requested_by: str | None = None,
) -> str:
    return format_venmo_confirmation_card(
        VenmoConfirmationCardView(
            request_id=request_id,
            attempt_number=attempt_number,
            status="pending",
            note=note,
            requested_by=requested_by,
        )
    ).caption


def venmo_confirmation_resolved_caption(
    *,
    request_id: int,
    status_label: str,
    display_name: str | None,
    note: str | None = None,
    requested_by: str | None = None,
) -> str:
    suffix = f" by {display_name}" if display_name else ""
    normalized = status_label.casefold()
    status = "confirmed" if "confirm" in normalized else "not_received"
    return format_venmo_confirmation_card(
        VenmoConfirmationCardView(
            request_id=request_id,
            status=status,
            note=note,
            requested_by=requested_by,
            confirmed_by=display_name if status == "confirmed" else None,
            not_received_by=display_name if status == "not_received" else None,
        )
    ).caption


def format_venmo_confirmation_confirmed_caption(
    *,
    request_id: int,
    confirmed_by: str | None,
    confirmed_at: datetime | None,
    note: str | None = None,
    requested_by: str | None = None,
) -> str:
    return format_venmo_confirmation_card(
        VenmoConfirmationCardView(
            request_id=request_id,
            status="confirmed",
            note=note,
            requested_by=requested_by,
            confirmed_by=confirmed_by,
            confirmed_at=confirmed_at,
        )
    ).caption


def format_venmo_confirmation_not_received_caption(
    *,
    request_id: int,
    note: str | None = None,
    requested_by: str | None = None,
    not_received_by: str | None = None,
) -> str:
    return format_venmo_confirmation_card(
        VenmoConfirmationCardView(
            request_id=request_id,
            status="not_received",
            note=note,
            requested_by=requested_by,
            not_received_by=not_received_by,
        )
    ).caption


def apply_telegram_caption_limit(
    body: str,
    *,
    note: str | None,
) -> VenmoConfirmationCaption:
    """Keep captions within Telegram's limit without truncating the note."""
    note_text = _canonical_note(note)
    if note_text:
        with_note = "\n".join([body, "", f"Note: {note_text}"])
        if len(with_note) <= TELEGRAM_CAPTION_MAX_LENGTH:
            return VenmoConfirmationCaption(caption=with_note)
        overflow_caption = "\n".join([body, "", VENMO_NOTE_OVERFLOW_MARKER])
        if len(overflow_caption) <= TELEGRAM_CAPTION_MAX_LENGTH:
            return VenmoConfirmationCaption(
                caption=overflow_caption,
                overflow_text=f"Note: {note_text}",
            )
        return VenmoConfirmationCaption(caption=body, overflow_text=f"Note: {note_text}")
    if len(body) <= TELEGRAM_CAPTION_MAX_LENGTH:
        return VenmoConfirmationCaption(caption=body)
    return VenmoConfirmationCaption(caption=body[:TELEGRAM_CAPTION_MAX_LENGTH])


def _pending_body(view: VenmoConfirmationCardView) -> str:
    lines = [
        f"Confirmation request #{view.request_id}",
        f"Attempt #{view.attempt_number or 1}",
    ]
    if view.requested_by:
        lines.append(f"Requested By: {view.requested_by}")
    lines.append("Was this evidence received/accepted?")
    return "\n".join(lines)


def _confirmed_body(view: VenmoConfirmationCardView) -> str:
    lines = [
        "✅✅ CONFIRMATION COMPLETED ✅✅",
        "🟢 CONFIRMED",
        "",
        f"Request ID: #{view.request_id}",
        "",
        "✅ EVIDENCE CONFIRMED",
    ]
    actors: list[str] = []
    if view.requested_by:
        actors.append(f"Requested By: {view.requested_by}")
    if view.confirmed_by:
        actors.append(f"Confirmed By: {view.confirmed_by}")
    if actors:
        lines.extend(["", *actors])
    if view.confirmed_at is not None:
        lines.append(f"Confirmed At: {view.confirmed_at.strftime('%Y-%m-%d %H:%M UTC')}")
    return "\n".join(lines)


def _not_received_body(view: VenmoConfirmationCardView) -> str:
    lines = [
        "⚠️ CONFIRMATION NOT RECEIVED",
        "🟡 FOLLOW-UP REQUIRED",
        "",
        f"Request ID: #{view.request_id}",
        "",
        "The evidence was marked Not Received.",
    ]
    actors: list[str] = []
    if view.requested_by:
        actors.append(f"Requested By: {view.requested_by}")
    if view.not_received_by:
        actors.append(f"Not Received By: {view.not_received_by}")
    if actors:
        lines.extend(["", *actors])
    return "\n".join(lines)


def _canonical_note(note: str | None) -> str | None:
    text = note.strip() if note else ""
    return text or None

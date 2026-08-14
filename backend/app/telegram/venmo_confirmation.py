from __future__ import annotations

from enum import StrEnum


class VenmoConfirmationCallbackAction(StrEnum):
    CONFIRM = "confirm"
    NOT_RECEIVED = "not_received"


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


def venmo_confirmation_caption(
    *,
    request_id: int,
    attempt_number: int,
    note: str | None,
) -> str:
    lines = [
        f"Confirmation request #{request_id}",
        f"Attempt #{attempt_number}",
    ]
    if note:
        lines.append(f"Note: {note}")
    lines.append("Was this evidence received/accepted?")
    return "\n".join(lines)


def venmo_confirmation_resolved_caption(
    *,
    request_id: int,
    status_label: str,
    display_name: str | None,
) -> str:
    suffix = f" by {display_name}" if display_name else ""
    return f"Confirmation request #{request_id}\n{status_label}{suffix}"

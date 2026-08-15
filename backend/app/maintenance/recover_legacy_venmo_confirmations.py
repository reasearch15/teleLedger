from __future__ import annotations

import argparse
import asyncio
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.models.inquiry_message import InquiryMessage
from app.models.user import User, UserRole
from app.models.venmo_confirmation import (
    VenmoConfirmationAttempt,
    VenmoConfirmationAttemptStatus,
    VenmoConfirmationEvent,
    VenmoConfirmationEventType,
    VenmoConfirmationRequest,
    VenmoConfirmationStatus,
)
from app.telegram.cashout_bot.api import TelegramBotFailureClass
from app.telegram.peer_ids import normalize_telegram_chat_id
from app.telegram.venmo_confirmation_delivery import classify_legacy_venmo_delivery_error


@dataclass(frozen=True, slots=True)
class LegacyRecoveryRow:
    request_id: int
    attempt_id: int
    error: str | None
    classification: str
    action: str
    reason: str | None = None
    scheduled_retry_at: datetime | None = None


async def recover_legacy_venmo_confirmations(
    *,
    apply: bool = False,
    session_factory: async_sessionmaker[Any] = SessionFactory,
    now: datetime | None = None,
) -> list[LegacyRecoveryRow]:
    recovery_time = now or datetime.now(UTC)
    rows: list[LegacyRecoveryRow] = []
    async with session_factory() as session, session.begin():
        result = await session.execute(_legacy_attempt_statement())
        for request, attempt, coadmin in result:
            classification = classify_legacy_venmo_delivery_error(attempt.last_error)
            skip_reason = _skip_reason(
                request=request,
                attempt=attempt,
                coadmin=coadmin,
                classification=classification,
                now=recovery_time,
            )
            if skip_reason is not None:
                rows.append(
                    LegacyRecoveryRow(
                        request_id=request.id,
                        attempt_id=attempt.id,
                        error=attempt.last_error,
                        classification=classification,
                        action="skip",
                        reason=skip_reason,
                    )
                )
                continue

            local_message = await _find_local_reconciled_message(
                session,
                request_id=request.id,
                attempt_number=attempt.attempt_number,
            )
            if local_message is not None:
                if apply:
                    _link_existing_message(
                        session=session,
                        request=request,
                        attempt=attempt,
                        message=local_message,
                        classification=classification,
                        recovered_at=recovery_time,
                    )
                rows.append(
                    LegacyRecoveryRow(
                        request_id=request.id,
                        attempt_id=attempt.id,
                        error=attempt.last_error,
                        classification=classification,
                        action="link_existing_message",
                        scheduled_retry_at=None,
                    )
                )
                continue

            if apply:
                previous_status = attempt.status.value
                previous_error = attempt.last_error
                attempt.status = VenmoConfirmationAttemptStatus.PENDING
                attempt.delivery_attempts = max(attempt.delivery_attempts or 0, 1)
                attempt.next_retry_at = recovery_time
                attempt.delivery_lease_until = None
                session.add(
                    VenmoConfirmationEvent(
                        request_id=request.id,
                        attempt_id=attempt.id,
                        event_type=VenmoConfirmationEventType.LEGACY_RECOVERY,
                        actor_source="system",
                        actor_identifier="legacy_venmo_recovery",
                        payload={
                            "previous_attempt_status": previous_status,
                            "previous_error": previous_error,
                            "classification": classification,
                            "scheduled_retry_at": recovery_time.isoformat(),
                            "recovered_at": recovery_time.isoformat(),
                        },
                    )
                )

            rows.append(
                LegacyRecoveryRow(
                    request_id=request.id,
                    attempt_id=attempt.id,
                    error=attempt.last_error,
                    classification=classification,
                    action="schedule_retry",
                    scheduled_retry_at=recovery_time,
                )
            )
    return rows


async def _find_local_reconciled_message(
    session: Any,
    *,
    request_id: int,
    attempt_number: int,
) -> InquiryMessage | None:
    chat_id = normalize_telegram_chat_id(get_settings().shared_telegram_supergroup_id)
    if chat_id is None:
        return None
    request_marker = f"Confirmation request #{request_id}"
    attempt_marker = f"Attempt #{attempt_number}"
    statement = (
        select(InquiryMessage)
        .where(
            InquiryMessage.telegram_chat_id == chat_id,
            InquiryMessage.caption.contains(request_marker),
            InquiryMessage.caption.contains(attempt_marker),
            InquiryMessage.is_deleted.is_(False),
        )
        .order_by(InquiryMessage.message_date.desc(), InquiryMessage.id.desc())
        .limit(1)
    )
    return (await session.execute(statement)).scalar_one_or_none()


def _link_existing_message(
    *,
    session: Any,
    request: VenmoConfirmationRequest,
    attempt: VenmoConfirmationAttempt,
    message: InquiryMessage,
    classification: str,
    recovered_at: datetime,
) -> None:
    previous_status = attempt.status.value
    previous_error = attempt.last_error
    attempt.status = VenmoConfirmationAttemptStatus.POSTED
    attempt.telegram_chat_id = message.telegram_chat_id
    attempt.telegram_message_id = message.telegram_message_id
    attempt.posted_at = recovered_at
    attempt.next_retry_at = None
    attempt.delivery_lease_until = None
    attempt.last_error = None
    event = VenmoConfirmationEvent(
        request_id=request.id,
        attempt_id=attempt.id,
        event_type=VenmoConfirmationEventType.LEGACY_RECOVERY,
        actor_source="system",
        actor_identifier="legacy_venmo_recovery",
        payload={
            "action": "link_existing_message",
            "previous_attempt_status": previous_status,
            "previous_error": previous_error,
            "classification": classification,
            "telegram_chat_id": message.telegram_chat_id,
            "telegram_message_id": message.telegram_message_id,
            "recovered_at": recovered_at.isoformat(),
        },
    )
    session.add(event)


def format_recovery_rows(rows: Sequence[LegacyRecoveryRow], *, apply: bool) -> str:
    mode = "apply" if apply else "dry-run"
    lines = [f"legacy_venmo_confirmation_recovery mode={mode} rows={len(rows)}"]
    for row in rows:
        fields = [
            f"request_id={row.request_id}",
            f"attempt_id={row.attempt_id}",
            f"classification={row.classification}",
            f"action={row.action}",
            f"error={_quote(row.error)}",
        ]
        if row.scheduled_retry_at is not None:
            fields.append(f"scheduled_retry_at={row.scheduled_retry_at.isoformat()}")
        if row.reason is not None:
            fields.append(f"reason={row.reason}")
        lines.append(" ".join(fields))
    return "\n".join(lines)


def _legacy_attempt_statement() -> Any:
    latest_attempt = (
        select(
            VenmoConfirmationAttempt.request_id.label("request_id"),
            func.max(VenmoConfirmationAttempt.attempt_number).label("attempt_number"),
        )
        .group_by(VenmoConfirmationAttempt.request_id)
        .subquery()
    )
    return (
        select(VenmoConfirmationRequest, VenmoConfirmationAttempt, User)
        .join(
            VenmoConfirmationAttempt,
            VenmoConfirmationAttempt.request_id == VenmoConfirmationRequest.id,
        )
        .join(
            latest_attempt,
            (latest_attempt.c.request_id == VenmoConfirmationAttempt.request_id)
            & (latest_attempt.c.attempt_number == VenmoConfirmationAttempt.attempt_number),
        )
        .join(User, User.id == VenmoConfirmationRequest.coadmin_id)
        .where(
            VenmoConfirmationRequest.status == VenmoConfirmationStatus.PENDING,
            VenmoConfirmationRequest.confirmed_at.is_(None),
            VenmoConfirmationAttempt.status == VenmoConfirmationAttemptStatus.FAILED_TO_SEND,
            VenmoConfirmationAttempt.telegram_message_id.is_(None),
        )
        .order_by(VenmoConfirmationRequest.id.asc(), VenmoConfirmationAttempt.id.asc())
        .with_for_update(of=VenmoConfirmationAttempt)
    )


def _skip_reason(
    *,
    request: VenmoConfirmationRequest,
    attempt: VenmoConfirmationAttempt,
    coadmin: User,
    classification: str,
    now: datetime,
) -> str | None:
    if normalize_telegram_chat_id(get_settings().shared_telegram_supergroup_id) is None:
        return "shared_telegram_supergroup_not_configured"
    if coadmin.role != UserRole.COADMIN or not coadmin.is_active:
        return "coadmin_inactive_or_invalid"
    if request.status != VenmoConfirmationStatus.PENDING or request.confirmed_at is not None:
        return "request_resolved"
    if attempt.telegram_message_id is not None:
        return "already_posted"
    if attempt.next_retry_at is not None:
        return "already_scheduled"
    if (
        attempt.delivery_lease_until is not None
        and _as_utc(attempt.delivery_lease_until) > _as_utc(now)
    ):
        return "active_lease"
    if classification != TelegramBotFailureClass.RETRYABLE.value:
        return "non_retryable_error"
    return None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _quote(value: str | None) -> str:
    if value is None:
        return "null"
    return repr(value[:240])


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Safely schedule legacy failed Venmo confirmation attempts for durable retry."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="Preview only; this is the default.")
    mode.add_argument("--apply", action="store_true", help="Apply validated recovery transitions.")
    return parser.parse_args(argv)


async def _main_async(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    rows = await recover_legacy_venmo_confirmations(apply=bool(args.apply))
    print(format_recovery_rows(rows, apply=bool(args.apply)))
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    return asyncio.run(_main_async(argv))


if __name__ == "__main__":
    raise SystemExit(main())

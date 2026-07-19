from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.repositories.payment_audit import PaymentAuditRepository
from app.db.repositories.payment_event import PaymentEventRepository
from app.db.repositories.telegram_message import TelegramMessageRepository
from app.models.payment_audit import PaymentAuditAction, PaymentAuditLog
from app.models.payment_event import PaymentEvent, PaymentStatus
from app.models.telegram_message import TelegramMessage
from app.parser.payment import parse_payment_message
from app.schemas.payment import ParsedPayment
from app.schemas.telegram import IncomingTelegramMessage
from app.services.base import ApplicationService

logger = get_logger(__name__)


class TelegramIngestionOutcome(StrEnum):
    """Observable result of ingesting one Telegram event."""

    PARSED = "parsed"
    IGNORED = "ignored"
    DUPLICATE = "duplicate"


@dataclass(frozen=True, slots=True)
class TelegramIngestionResult:
    """Identifiers, persistence facts, and outcome returned to an adapter."""

    outcome: TelegramIngestionOutcome
    telegram_message_id: int
    payment_event_id: int | None
    parsed_payment: ParsedPayment | None
    existing_raw_message: bool
    existing_payment_event: bool
    parser_matched: bool
    raw_message_inserted: bool
    payment_inserted: bool
    reason_skipped: str | None


class TelegramIngestionService(ApplicationService):
    """Atomically persist, deduplicate, and parse incoming Telegram messages."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._message_repository = TelegramMessageRepository(session)
        self._payment_repository = PaymentEventRepository(session)
        self._audit_repository = PaymentAuditRepository(session)

    async def ingest(
        self,
        incoming: IncomingTelegramMessage,
    ) -> TelegramIngestionResult:
        """Store a raw message and create a payment event when parsing succeeds."""
        async with self._session.begin():
            message, created = await self._message_repository.add_if_absent(
                TelegramMessage(
                    telegram_chat_id=incoming.telegram_chat_id,
                    telegram_message_id=incoming.telegram_message_id,
                    sender_id=incoming.sender_id,
                    sender_name=incoming.sender_name,
                    raw_text=incoming.raw_text,
                    received_at=incoming.received_at,
                )
            )
            raw_message_refreshed = False

            existing_payment = (
                await self._payment_repository.get_one_by_telegram_message_id(message.id)
            )
            if existing_payment is not None:
                return TelegramIngestionResult(
                    outcome=TelegramIngestionOutcome.DUPLICATE,
                    telegram_message_id=message.id,
                    payment_event_id=existing_payment.id,
                    parsed_payment=None,
                    existing_raw_message=not created,
                    existing_payment_event=True,
                    parser_matched=True,
                    raw_message_inserted=created,
                    payment_inserted=False,
                    reason_skipped="raw message and payment_event already exist",
                )

            if not created and message.raw_text != incoming.raw_text:
                message.sender_id = incoming.sender_id
                message.sender_name = incoming.sender_name
                message.raw_text = incoming.raw_text
                message.received_at = incoming.received_at
                await self._message_repository.flush(message)
                raw_message_refreshed = True
                logger.info(
                    "telegram_raw_message_refreshed",
                    extra={
                        "telegram_message_row_id": message.id,
                        "telegram_message_id": incoming.telegram_message_id,
                        "telegram_chat_id": incoming.telegram_chat_id,
                    },
                )

            # Parse the persisted body. Existing non-payment rows can now repair
            # themselves when a later Telegram edit or backfill sees payment text.
            parsed = parse_payment_message(message.raw_text)
            if parsed is None:
                if "you received" in message.raw_text.lower():
                    logger.warning(
                        "telegram_payment_like_message_rejected",
                        extra={
                            "telegram_message_row_id": message.id,
                            "telegram_message_id": incoming.telegram_message_id,
                            "telegram_chat_id": incoming.telegram_chat_id,
                            "reason": "parser did not match",
                            "raw_message_inserted": created,
                            "raw_message_refreshed": raw_message_refreshed,
                        },
                    )
                return TelegramIngestionResult(
                    outcome=TelegramIngestionOutcome.IGNORED,
                    telegram_message_id=message.id,
                    payment_event_id=None,
                    parsed_payment=None,
                    existing_raw_message=not created,
                    existing_payment_event=False,
                    parser_matched=False,
                    raw_message_inserted=created,
                    payment_inserted=False,
                    reason_skipped="parser did not match",
                )

            payment_event = await self._payment_repository.add(
                PaymentEvent(
                    telegram_message_id=message.id,
                    recipient_tag=parsed.recipient_tag,
                    amount=parsed.amount,
                    payment_sender_name=parsed.payment_sender_name,
                    payment_datetime=parsed.payment_datetime,
                    total_in=parsed.total_in,
                    total_out=parsed.total_out,
                    raw_text=message.raw_text,
                    status=PaymentStatus.PENDING,
                    parser_confidence=100,
                )
            )
            await self._audit_repository.add(
                PaymentAuditLog(
                    payment_event_id=payment_event.id,
                    actor_user_id=None,
                    subject_staff_id=None,
                    action=PaymentAuditAction.CREATED,
                    from_status=None,
                    to_status=PaymentStatus.PENDING,
                )
            )
            return TelegramIngestionResult(
                outcome=TelegramIngestionOutcome.PARSED,
                telegram_message_id=message.id,
                payment_event_id=payment_event.id,
                parsed_payment=parsed,
                existing_raw_message=not created,
                existing_payment_event=False,
                parser_matched=True,
                raw_message_inserted=created,
                payment_inserted=True,
                reason_skipped=None,
            )

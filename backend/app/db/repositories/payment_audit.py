from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import aliased

from app.db.repositories.base import BaseRepository
from app.models.payment_audit import PaymentAuditLog
from app.models.user import User


@dataclass(frozen=True, slots=True)
class PaymentAuditRecord:
    """Audit row with preserved actor and subject display names."""

    audit: PaymentAuditLog
    actor_username: str | None
    subject_username: str | None


class PaymentAuditRepository(BaseRepository[PaymentAuditLog]):
    """Append-only payment workflow audit persistence."""

    async def add(self, audit: PaymentAuditLog) -> PaymentAuditLog:
        self._session.add(audit)
        await self._session.flush()
        return audit

    async def list_for_payment(
        self,
        payment_event_id: int,
    ) -> list[PaymentAuditRecord]:
        actor = aliased(User, name="audit_actor")
        subject = aliased(User, name="audit_subject")
        statement = (
            select(PaymentAuditLog, actor.username, subject.username)
            .outerjoin(actor, PaymentAuditLog.actor_user_id == actor.id)
            .outerjoin(subject, PaymentAuditLog.subject_staff_id == subject.id)
            .where(PaymentAuditLog.payment_event_id == payment_event_id)
            .order_by(PaymentAuditLog.created_at.asc(), PaymentAuditLog.id.asc())
        )
        rows = (await self._session.execute(statement)).all()
        return [
            PaymentAuditRecord(
                audit=row[0],
                actor_username=row[1],
                subject_username=row[2],
            )
            for row in rows
        ]

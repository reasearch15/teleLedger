from __future__ import annotations

from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.payment_audit import (
    PaymentAuditRecord,
    PaymentAuditRepository,
)
from app.db.repositories.payment_event import PaymentEventRepository, PaymentListPage
from app.db.repositories.user import UserRepository
from app.models.payment_audit import PaymentAuditAction, PaymentAuditLog
from app.models.payment_dismissal import PaymentEventCoadminDismissal
from app.models.payment_event import PaymentEvent, PaymentStatus
from app.models.user import User, UserRole
from app.services.base import ApplicationService
from app.websocket.events import LiveEventType, event_broker


class PaymentNotFoundError(Exception):
    """Raised when a requested payment event does not exist."""


class PaymentStateConflictError(Exception):
    """Raised when an action is invalid for the payment's current state."""


class InvalidPaymentFilterError(Exception):
    """Raised when list filter bounds are contradictory."""


class PaymentAuthorizationError(Exception):
    """Raised when a user is not allowed to perform a workflow action."""


class AssignmentStaffNotFoundError(Exception):
    """Raised when an administrator selects an unavailable staff account."""


class StaffCoadminRequiredError(Exception):
    """Raised when a staff-only operation requires coadmin assignment."""


class PaymentService(ApplicationService):
    """Transactional payment query and workflow operations."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = PaymentEventRepository(session)
        self._audit_repository = PaymentAuditRepository(session)
        self._user_repository = UserRepository(session)

    async def list_payments(
        self,
        *,
        status: PaymentStatus | None = None,
        search: str | None = None,
        date_from: date | None = None,
        date_to: date | None = None,
        limit: int = 7,
        offset: int = 0,
        include_total: bool = False,
        active_only: bool = False,
        current_user: User,
    ) -> PaymentListPage:
        """Return a bounded page, counting only when explicitly requested."""
        if date_from is not None and date_to is not None and date_from > date_to:
            raise InvalidPaymentFilterError("date_from must not be after date_to")

        normalized_search = search.strip() if search else None
        return await self._repository.list_payments(
            status=status,
            search=normalized_search or None,
            date_from=date_from,
            date_to=date_to,
            limit=limit,
            offset=offset,
            include_total=include_total,
            active_only=active_only,
            visible_to_staff_id=(
                current_user.id
                if current_user.role == UserRole.STAFF
                else None
            ),
            visible_to_coadmin_id=(
                current_user.coadmin_id
                if current_user.role == UserRole.STAFF
                else None
            ),
            include_dismissals=current_user.role == UserRole.ADMIN,
            exclude_all_coadmins_declined=True,
        )

    async def dismiss_not_ours(self, payment_event_id: int, actor: User) -> None:
        """Dismiss a pending payment for the acting staff member's coadmin team."""
        self._require_staff(actor)
        if actor.coadmin_id is None:
            raise StaffCoadminRequiredError(
                "Staff must be assigned to a coadmin before dismissing payments."
            )
        all_coadmins_declined = False
        async with self._session.begin():
            payment = await self._get_locked(payment_event_id)
            if payment.status != PaymentStatus.PENDING:
                raise PaymentStateConflictError(
                    "Only pending payments can be marked Not Ours."
                )
            existing = await self._session.scalar(
                select(PaymentEventCoadminDismissal).where(
                    PaymentEventCoadminDismissal.payment_event_id == payment.id,
                    PaymentEventCoadminDismissal.coadmin_id == actor.coadmin_id,
                )
            )
            if existing is None:
                self._session.add(
                    PaymentEventCoadminDismissal(
                        payment_event_id=payment.id,
                        coadmin_id=actor.coadmin_id,
                        dismissed_by_staff_id=actor.id,
                    )
                )
            all_coadmins_declined = await self._mark_all_coadmins_declined_if_complete(
                payment
            )
        await event_broker.publish(
            LiveEventType.PAYMENT_DISMISSED,
            payment_id=payment_event_id,
            coadmin_id=actor.coadmin_id,
        )
        if all_coadmins_declined:
            await event_broker.publish(
                LiveEventType.PAYMENT_ALL_COADMINS_DECLINED,
                payment_id=payment_event_id,
            )

    async def list_declined_review(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        include_total: bool = False,
        current_user: User,
    ) -> PaymentListPage:
        """Return payments declined by every active coadmin awaiting admin review."""
        self._require_admin(current_user)
        return await self._repository.list_payments(
            limit=limit,
            offset=offset,
            include_total=include_total,
            include_dismissals=True,
            admin_review_only=True,
        )

    async def dismiss_declined_review(
        self,
        payment_event_id: int,
        actor: User,
    ) -> None:
        """Remove a fully declined payment from the admin review queue."""
        self._require_admin(actor)
        async with self._session.begin():
            payment = await self._get_locked(payment_event_id)
            if payment.all_coadmins_declined_at is None:
                raise PaymentStateConflictError(
                    "Only payments declined by all coadmins can be dismissed."
                )
            if payment.declined_review_dismissed_at is not None:
                return
            payment.declined_review_dismissed_at = datetime.now(UTC)
            await self._repository.flush(payment)
        await event_broker.publish(
            LiveEventType.PAYMENT_DECLINED_REVIEW_DISMISSED,
            payment_id=payment_event_id,
        )

    async def delete_payment(self, payment_event_id: int, actor: User) -> None:
        """Permanently delete a payment and related coadmin dismissal records."""
        self._require_admin(actor)
        async with self._session.begin():
            payment = await self._get_locked(payment_event_id)
            if payment.all_coadmins_declined_at is None:
                raise PaymentStateConflictError(
                    "Only payments declined by all coadmins can be deleted."
                )
            await self._repository.delete(payment)
        await event_broker.publish(
            LiveEventType.PAYMENT_DELETED,
            payment_id=payment_event_id,
        )

    async def list_history(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        include_total: bool = False,
        current_user: User,
    ) -> PaymentListPage:
        """Return claimed and completed payments across all staff."""
        self._require_admin(current_user)
        return await self._repository.list_payments(
            limit=limit,
            offset=offset,
            include_total=include_total,
            history_only=True,
            include_dismissals=True,
        )

    async def list_my_history(
        self,
        *,
        limit: int = 20,
        offset: int = 0,
        include_total: bool = False,
        current_user: User,
    ) -> PaymentListPage:
        """Return payments claimed or completed by the current staff member."""
        self._require_staff(current_user)
        return await self._repository.list_payments(
            limit=limit,
            offset=offset,
            include_total=include_total,
            history_staff_id=current_user.id,
        )

    async def claim(self, payment_event_id: int, actor: User) -> PaymentEvent:
        """Atomically claim a pending payment for one staff member."""
        self._require_staff(actor)
        async with self._session.begin():
            payment = await self._get_locked(payment_event_id)
            if payment.status != PaymentStatus.PENDING:
                raise PaymentStateConflictError(
                    "This payment has already been claimed."
                )
            payment.status = PaymentStatus.IN_PROGRESS
            payment.claimed_by_staff_id = actor.id
            payment.claimed_at = datetime.now(UTC)
            await self._repository.flush(payment)
            await self._record_audit(
                payment,
                actor=actor,
                subject_staff_id=actor.id,
                action=PaymentAuditAction.CLAIMED,
                from_status=PaymentStatus.PENDING,
            )
        await event_broker.publish(
            LiveEventType.PAYMENT_CLAIMED,
            payment_id=payment.id,
        )
        return payment

    async def mark_done(self, payment_event_id: int, actor: User) -> PaymentEvent:
        """Complete only a payment currently claimed by the acting staff."""
        self._require_staff(actor)
        async with self._session.begin():
            payment = await self._get_locked(payment_event_id)
            if (
                payment.status != PaymentStatus.IN_PROGRESS
                or payment.claimed_by_staff_id != actor.id
            ):
                raise PaymentStateConflictError(
                    "Only the staff member who claimed this payment can mark it Done."
                )
            payment.status = PaymentStatus.DONE
            payment.completed_by_staff_id = actor.id
            payment.completed_at = datetime.now(UTC)
            await self._repository.flush(payment)
            await self._record_audit(
                payment,
                actor=actor,
                subject_staff_id=actor.id,
                action=PaymentAuditAction.DONE,
                from_status=PaymentStatus.IN_PROGRESS,
            )
        await event_broker.publish(
            LiveEventType.PAYMENT_DONE,
            payment_id=payment.id,
        )
        await event_broker.publish(LiveEventType.LEDGER_CHANGED)
        return payment

    async def unclaim(self, payment_event_id: int, actor: User) -> PaymentEvent:
        """Allow staff to release only their own in-progress payment."""
        self._require_staff(actor)
        async with self._session.begin():
            payment = await self._get_locked(payment_event_id)
            if (
                payment.status != PaymentStatus.IN_PROGRESS
                or payment.claimed_by_staff_id != actor.id
            ):
                raise PaymentStateConflictError(
                    "Only the staff member who claimed this payment can unclaim it."
                )
            previous_staff_id = payment.claimed_by_staff_id
            payment.status = PaymentStatus.PENDING
            payment.claimed_by_staff_id = None
            payment.claimed_at = None
            await self._repository.flush(payment)
            await self._record_audit(
                payment,
                actor=actor,
                subject_staff_id=previous_staff_id,
                action=PaymentAuditAction.UNCLAIMED,
                from_status=PaymentStatus.IN_PROGRESS,
            )
        await event_broker.publish(
            LiveEventType.PAYMENT_UNCLAIMED,
            payment_id=payment.id,
        )
        return payment

    async def force_unclaim(self, payment_event_id: int, actor: User) -> PaymentEvent:
        """Allow an administrator to return a claimed payment to Pending."""
        self._require_admin(actor)
        async with self._session.begin():
            payment = await self._get_locked(payment_event_id)
            if payment.status != PaymentStatus.IN_PROGRESS:
                raise PaymentStateConflictError("Only claimed payments can be unclaimed.")
            previous_staff_id = payment.claimed_by_staff_id
            payment.status = PaymentStatus.PENDING
            payment.claimed_by_staff_id = None
            payment.claimed_at = None
            await self._repository.flush(payment)
            await self._record_audit(
                payment,
                actor=actor,
                subject_staff_id=previous_staff_id,
                action=PaymentAuditAction.UNCLAIMED,
                from_status=PaymentStatus.IN_PROGRESS,
            )
        await event_broker.publish(
            LiveEventType.PAYMENT_UNCLAIMED,
            payment_id=payment.id,
        )
        return payment

    async def reopen(self, payment_event_id: int, actor: User) -> PaymentEvent:
        """Allow an administrator to reopen a completed payment as Pending."""
        self._require_admin(actor)
        async with self._session.begin():
            payment = await self._get_locked(payment_event_id)
            if payment.status != PaymentStatus.DONE:
                raise PaymentStateConflictError("Only Done payments can be reopened.")
            previous_staff_id = payment.completed_by_staff_id
            payment.status = PaymentStatus.PENDING
            payment.claimed_by_staff_id = None
            payment.claimed_at = None
            payment.completed_by_staff_id = None
            payment.completed_at = None
            await self._repository.flush(payment)
            await self._record_audit(
                payment,
                actor=actor,
                subject_staff_id=previous_staff_id,
                action=PaymentAuditAction.REOPENED,
                from_status=PaymentStatus.DONE,
            )
        await event_broker.publish(
            LiveEventType.PAYMENT_REOPENED,
            payment_id=payment.id,
        )
        return payment

    async def assign(
        self,
        payment_event_id: int,
        staff_id: int,
        actor: User,
    ) -> PaymentEvent:
        """Assign or reassign a non-completed payment to active staff."""
        self._require_admin(actor)
        async with self._session.begin():
            staff = await self._user_repository.get_active_staff(staff_id)
            if staff is None:
                raise AssignmentStaffNotFoundError(
                    f"Active staff user {staff_id} was not found"
                )
            payment = await self._get_locked(payment_event_id)
            if payment.status == PaymentStatus.DONE:
                raise PaymentStateConflictError(
                    "Reopen a Done payment before assigning it."
                )
            previous_status = payment.status
            payment.status = PaymentStatus.IN_PROGRESS
            payment.claimed_by_staff_id = staff.id
            payment.claimed_at = datetime.now(UTC)
            await self._repository.flush(payment)
            await self._record_audit(
                payment,
                actor=actor,
                subject_staff_id=staff.id,
                action=PaymentAuditAction.REASSIGNED,
                from_status=previous_status,
            )
        await event_broker.publish(
            LiveEventType.PAYMENT_CLAIMED,
            payment_id=payment.id,
        )
        return payment

    async def list_audit(
        self,
        payment_event_id: int,
        actor: User,
    ) -> list[PaymentAuditRecord]:
        self._require_admin(actor)
        if await self._repository.get_by_id(payment_event_id) is None:
            raise PaymentNotFoundError(f"Payment event {payment_event_id} was not found")
        return await self._audit_repository.list_for_payment(payment_event_id)

    async def _record_audit(
        self,
        payment: PaymentEvent,
        *,
        actor: User | None,
        subject_staff_id: int | None,
        action: PaymentAuditAction,
        from_status: PaymentStatus | None,
    ) -> None:
        await self._audit_repository.add(
            PaymentAuditLog(
                payment_event_id=payment.id,
                actor_user_id=actor.id if actor is not None else None,
                subject_staff_id=subject_staff_id,
                action=action,
                from_status=from_status,
                to_status=payment.status,
            )
        )

    @staticmethod
    def _require_staff(actor: User) -> None:
        if actor.role != UserRole.STAFF:
            raise PaymentAuthorizationError("Staff access is required.")

    @staticmethod
    def _require_admin(actor: User) -> None:
        if actor.role != UserRole.ADMIN:
            raise PaymentAuthorizationError("Administrator access is required.")

    async def _get_locked(self, payment_event_id: int) -> PaymentEvent:
        payment = await self._repository.get_by_id_for_update(payment_event_id)
        if payment is None:
            raise PaymentNotFoundError(f"Payment event {payment_event_id} was not found")
        return payment

    async def _mark_all_coadmins_declined_if_complete(
        self,
        payment: PaymentEvent,
    ) -> bool:
        """Mark a payment declined by all active coadmins when every team has dismissed."""
        if payment.all_coadmins_declined_at is not None:
            return False
        active_coadmin_ids = await self._user_repository.list_active_coadmin_ids()
        if not active_coadmin_ids:
            return False
        dismissal_count = await self._repository.count_coadmin_dismissals_for_active_coadmins(
            payment.id,
            active_coadmin_ids,
        )
        if dismissal_count < len(active_coadmin_ids):
            return False
        payment.all_coadmins_declined_at = datetime.now(UTC)
        await self._repository.flush(payment)
        return True

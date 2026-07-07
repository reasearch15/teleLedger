from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import (
    hash_password,
    normalize_username,
    run_dummy_password_check,
    staff_color_for_username,
    verify_password,
)
from app.db.repositories.user import UserRepository
from app.models.cashout import CashoutRequest, CashoutRequestAudit
from app.models.payment_audit import PaymentAuditLog
from app.models.payment_dismissal import PaymentEventCoadminDismissal
from app.models.payment_event import PaymentEvent
from app.models.staff_settlement import StaffSettlement, StaffSettlementAuditLog
from app.models.user import User, UserRole
from app.services.base import ApplicationService
from app.websocket.events import LiveEventType, event_broker


class InvalidCredentialsError(Exception):
    """Raised when login credentials cannot authenticate an active user."""


class UsernameAlreadyExistsError(Exception):
    """Raised when a normalized username is already registered."""


class StaffNotFoundError(Exception):
    """Raised when an administrative staff target does not exist."""


class CoadminNotFoundError(Exception):
    """Raised when an administrative coadmin target does not exist."""


class StaffSelfDeleteForbiddenError(Exception):
    """Raised when an administrator attempts to delete their own account."""


class AuthService(ApplicationService):
    """Local authentication and first-admin creation workflows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = UserRepository(session)

    async def authenticate(self, username: str, password: str) -> User:
        """Verify credentials and record a successful login atomically."""
        normalized_username = normalize_username(username)
        async with self._session.begin():
            user = await self._repository.get_by_username(normalized_username)
            if user is None:
                await asyncio.to_thread(run_dummy_password_check, password)
                raise InvalidCredentialsError("Invalid username or password")

            valid, replacement_hash = await asyncio.to_thread(
                verify_password,
                password,
                user.password_hash,
            )
            if not valid or not user.is_active:
                raise InvalidCredentialsError("Invalid username or password")

            if replacement_hash is not None:
                user.password_hash = replacement_hash
            user.last_login_at = datetime.now(UTC)
            await self._repository.flush(user)
            return user

    async def get_active_user(self, user_id: int) -> User:
        """Resolve a session subject and enforce current account activity."""
        user = await self._repository.get_by_id(user_id)
        if user is None or not user.is_active:
            raise InvalidCredentialsError("Invalid or expired session")
        return user

    async def create_admin(self, username: str, password: str) -> User:
        """Create a local administrator through the trusted CLI path."""
        return await self._create_user(username, password, UserRole.ADMIN)

    async def _create_user(
        self,
        username: str,
        password: str,
        role: UserRole,
    ) -> User:
        normalized_username = normalize_username(username)
        password_hash = await asyncio.to_thread(hash_password, password)
        try:
            async with self._session.begin():
                existing = await self._repository.get_by_username(normalized_username)
                if existing is not None:
                    raise UsernameAlreadyExistsError("Username is already registered")
                return await self._repository.add(
                    User(
                        username=normalized_username,
                        password_hash=password_hash,
                        role=role,
                        is_active=True,
                        staff_color=staff_color_for_username(normalized_username),
                    )
                )
        except IntegrityError as error:
            raise UsernameAlreadyExistsError("Username is already registered") from error


class StaffManagementService(ApplicationService):
    """Admin-only staff account management workflows."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = UserRepository(session)

    async def list_staff(self) -> list[User]:
        """Return all staff accounts."""
        return await self._repository.list_staff()

    async def list_coadmins(self) -> list[User]:
        """Return all coadmin accounts."""
        return await self._repository.list_coadmins()

    async def create_coadmin(
        self,
        username: str,
        password: str,
        *,
        is_active: bool = True,
    ) -> User:
        """Create a coadmin account that can own multiple staff users."""
        normalized_username = normalize_username(username)
        password_hash = await asyncio.to_thread(hash_password, password)
        coadmin: User
        try:
            async with self._session.begin():
                existing = await self._repository.get_by_username(normalized_username)
                if existing is not None:
                    raise UsernameAlreadyExistsError("Username is already registered")
                coadmin = await self._repository.add(
                    User(
                        username=normalized_username,
                        password_hash=password_hash,
                        role=UserRole.COADMIN,
                        is_active=is_active,
                        staff_color=staff_color_for_username(normalized_username),
                    )
                )
        except IntegrityError as error:
            raise UsernameAlreadyExistsError("Username is already registered") from error
        await event_broker.publish(
            LiveEventType.STAFF_CHANGED,
            user_id=coadmin.id,
        )
        return coadmin

    async def create_staff(
        self,
        username: str,
        password: str,
        *,
        coadmin_id: int,
    ) -> User:
        """Create an active staff account."""
        normalized_username = normalize_username(username)
        password_hash = await asyncio.to_thread(hash_password, password)
        staff: User
        try:
            async with self._session.begin():
                existing = await self._repository.get_by_username(normalized_username)
                if existing is not None:
                    raise UsernameAlreadyExistsError("Username is already registered")
                coadmin = await self._repository.get_active_coadmin(coadmin_id)
                if coadmin is None:
                    raise CoadminNotFoundError(
                        f"Active coadmin user {coadmin_id} was not found"
                    )
                staff = await self._repository.add(
                    User(
                        username=normalized_username,
                        password_hash=password_hash,
                        role=UserRole.STAFF,
                        is_active=True,
                        staff_color=staff_color_for_username(normalized_username),
                        coadmin_id=coadmin.id,
                    )
                )
        except IntegrityError as error:
            raise UsernameAlreadyExistsError("Username is already registered") from error
        await event_broker.publish(
            LiveEventType.STAFF_CHANGED,
            user_id=staff.id,
        )
        return staff

    async def assign_staff_coadmin(self, user_id: int, coadmin_id: int) -> User:
        """Assign an existing staff account to an active coadmin."""
        async with self._session.begin():
            staff = await self._repository.get_staff_for_update(user_id)
            if staff is None:
                raise StaffNotFoundError(f"Staff user {user_id} was not found")
            coadmin = await self._repository.get_active_coadmin(coadmin_id)
            if coadmin is None:
                raise CoadminNotFoundError(
                    f"Active coadmin user {coadmin_id} was not found"
                )
            staff.coadmin_id = coadmin.id
            await self._repository.flush(staff)
        await event_broker.publish(
            LiveEventType.STAFF_CHANGED,
            user_id=staff.id,
        )
        return staff

    async def disable_staff(self, user_id: int) -> User:
        """Disable a staff account with a row-level lock."""
        async with self._session.begin():
            user = await self._repository.get_staff_for_update(user_id)
            if user is None:
                raise StaffNotFoundError(f"Staff user {user_id} was not found")
            user.is_active = False
            await self._repository.flush(user)
        await event_broker.publish(
            LiveEventType.STAFF_CHANGED,
            user_id=user.id,
        )
        return user

    async def reset_password(self, user_id: int, password: str) -> User:
        """Replace a staff password with a freshly generated hash."""
        password_hash = await asyncio.to_thread(hash_password, password)
        async with self._session.begin():
            user = await self._repository.get_staff_for_update(user_id)
            if user is None:
                raise StaffNotFoundError(f"Staff user {user_id} was not found")
            user.password_hash = password_hash
            await self._repository.flush(user)
        await event_broker.publish(
            LiveEventType.STAFF_CHANGED,
            user_id=user.id,
        )
        return user

    async def delete_staff(self, user_id: int, *, actor: User) -> None:
        """Permanently remove a staff account and detach historical references."""
        if user_id == actor.id:
            raise StaffSelfDeleteForbiddenError(
                "Administrators cannot delete their own account."
            )
        async with self._session.begin():
            user = await self._repository.get_staff_for_update(user_id)
            if user is None:
                raise StaffNotFoundError(f"Staff user {user_id} was not found")
            await self._detach_staff_references(user_id)
            await self._repository.delete(user)
        await event_broker.publish(
            LiveEventType.STAFF_CHANGED,
            user_id=user_id,
        )

    async def _detach_staff_references(self, user_id: int) -> None:
        """Clear staff links from operational records before hard-deleting the user."""
        await self._session.execute(
            update(PaymentEvent)
            .where(PaymentEvent.claimed_by_staff_id == user_id)
            .values(claimed_by_staff_id=None)
        )
        await self._session.execute(
            update(PaymentEvent)
            .where(PaymentEvent.completed_by_staff_id == user_id)
            .values(completed_by_staff_id=None)
        )
        await self._session.execute(
            update(PaymentAuditLog)
            .where(PaymentAuditLog.actor_user_id == user_id)
            .values(actor_user_id=None)
        )
        await self._session.execute(
            update(PaymentAuditLog)
            .where(PaymentAuditLog.subject_staff_id == user_id)
            .values(subject_staff_id=None)
        )
        await self._session.execute(
            update(PaymentEventCoadminDismissal)
            .where(PaymentEventCoadminDismissal.dismissed_by_staff_id == user_id)
            .values(dismissed_by_staff_id=None)
        )
        await self._session.execute(
            update(CashoutRequest)
            .where(CashoutRequest.created_by_staff_id == user_id)
            .values(created_by_staff_id=None)
        )
        await self._session.execute(
            update(CashoutRequest)
            .where(CashoutRequest.completed_by_staff_id == user_id)
            .values(completed_by_staff_id=None)
        )
        await self._session.execute(
            update(CashoutRequestAudit)
            .where(CashoutRequestAudit.actor_user_id == user_id)
            .values(actor_user_id=None)
        )
        await self._session.execute(
            update(StaffSettlement)
            .where(StaffSettlement.staff_id == user_id)
            .values(staff_id=None)
        )
        await self._session.execute(
            update(StaffSettlementAuditLog)
            .where(StaffSettlementAuditLog.actor_user_id == user_id)
            .values(actor_user_id=None)
        )

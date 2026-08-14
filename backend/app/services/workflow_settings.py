from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.workflow_settings import CoadminTelegramWorkflowSettingsRepository
from app.models.user import User, UserRole
from app.models.workflow_settings import CoadminTelegramWorkflowSettings
from app.services.base import ApplicationService


class WorkflowSettingsAuthorizationError(Exception):
    """Raised when an actor cannot access workflow settings."""


class WorkflowSettingsValidationError(Exception):
    """Raised when workflow settings would split the shared Telegram group."""


class WorkflowSettingsService(ApplicationService):
    """Per-coadmin Telegram workflow settings access."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session
        self._repository = CoadminTelegramWorkflowSettingsRepository(session)

    async def get_for_coadmin(
        self,
        *,
        coadmin_id: int,
        actor: User,
    ) -> CoadminTelegramWorkflowSettings | None:
        self._require_coadmin_context(actor, coadmin_id)
        return await self._repository.get_for_coadmin(coadmin_id)

    async def upsert_for_coadmin(
        self,
        *,
        coadmin_id: int,
        cashout_group_id: int | None,
        venmo_confirmation_group_id: int | None,
        actor: User,
    ) -> CoadminTelegramWorkflowSettings:
        self._require_admin(actor)
        shared_supergroup_id = self._resolve_shared_supergroup_id(
            cashout_group_id,
            venmo_confirmation_group_id,
        )
        settings = await self._repository.get_for_coadmin(coadmin_id)
        if settings is None:
            settings = await self._repository.add(
                CoadminTelegramWorkflowSettings(
                    coadmin_id=coadmin_id,
                    cashout_group_id=shared_supergroup_id,
                    venmo_confirmation_group_id=shared_supergroup_id,
                )
            )
        else:
            settings.cashout_group_id = shared_supergroup_id
            settings.venmo_confirmation_group_id = shared_supergroup_id
            await self._session.flush()
        return settings

    @staticmethod
    def _resolve_shared_supergroup_id(
        cashout_group_id: int | None,
        venmo_confirmation_group_id: int | None,
    ) -> int | None:
        if (
            cashout_group_id is not None
            and venmo_confirmation_group_id is not None
            and cashout_group_id != venmo_confirmation_group_id
        ):
            raise WorkflowSettingsValidationError(
                "Cashout tasks and Venmo confirmations must use the shared Telegram supergroup."
            )
        if cashout_group_id is not None:
            return cashout_group_id
        return venmo_confirmation_group_id

    @staticmethod
    def _require_admin(actor: User) -> None:
        if actor.role != UserRole.ADMIN:
            raise WorkflowSettingsAuthorizationError("Administrator access is required.")

    @staticmethod
    def _require_coadmin_context(actor: User, coadmin_id: int) -> None:
        if actor.role == UserRole.ADMIN:
            return
        if actor.role == UserRole.COADMIN and actor.id == coadmin_id:
            return
        if actor.role == UserRole.STAFF and actor.coadmin_id == coadmin_id:
            return
        raise WorkflowSettingsAuthorizationError("Workflow settings are not accessible.")

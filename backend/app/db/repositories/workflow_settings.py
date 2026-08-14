from __future__ import annotations

from sqlalchemy import select

from app.db.repositories.base import BaseRepository
from app.models.workflow_settings import CoadminTelegramWorkflowSettings


class CoadminTelegramWorkflowSettingsRepository(BaseRepository[CoadminTelegramWorkflowSettings]):
    """Persistence helpers for per-coadmin Telegram workflow settings."""

    async def get_for_coadmin(self, coadmin_id: int) -> CoadminTelegramWorkflowSettings | None:
        statement = select(CoadminTelegramWorkflowSettings).where(
            CoadminTelegramWorkflowSettings.coadmin_id == coadmin_id
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def add(
        self,
        settings: CoadminTelegramWorkflowSettings,
    ) -> CoadminTelegramWorkflowSettings:
        self._session.add(settings)
        await self._session.flush()
        return settings

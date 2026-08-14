from __future__ import annotations

from sqlalchemy import select

from app.db.repositories.base import BaseRepository
from app.models.media_asset import MediaAsset


class MediaAssetRepository(BaseRepository[MediaAsset]):
    """Persistence helpers for durable media metadata."""

    async def add(self, asset: MediaAsset) -> MediaAsset:
        self._session.add(asset)
        await self._session.flush()
        return asset

    async def get_by_id(self, asset_id: int) -> MediaAsset | None:
        statement = select(MediaAsset).where(MediaAsset.id == asset_id)
        return (await self._session.execute(statement)).scalar_one_or_none()

    async def get_for_coadmin(self, asset_id: int, coadmin_id: int) -> MediaAsset | None:
        statement = select(MediaAsset).where(
            MediaAsset.id == asset_id,
            MediaAsset.coadmin_id == coadmin_id,
        )
        return (await self._session.execute(statement)).scalar_one_or_none()

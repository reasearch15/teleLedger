from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession


class BaseRepository[ModelT]:
    """Base for future repositories; owns persistence access, not business rules."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

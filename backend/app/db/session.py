from __future__ import annotations

from collections.abc import AsyncIterator

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import get_settings

settings = get_settings()

engine = create_async_engine(
    settings.database_url,
    echo=settings.database_echo,
    pool_pre_ping=True,
    pool_recycle=1800,
    pool_timeout=30,
    pool_size=settings.database_pool_size,
    max_overflow=settings.database_max_overflow,
)

SessionFactory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


async def get_session() -> AsyncIterator[AsyncSession]:
    """Provide a request-scoped database session."""
    async with SessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def get_auth_session() -> AsyncIterator[AsyncSession]:
    """Provide an isolated session for cookie authentication lookups."""
    async with SessionFactory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise


async def close_database() -> None:
    """Release all pooled database connections."""
    await engine.dispose()


async def warm_database_pool() -> None:
    """Open one pooled connection before development requests begin."""
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))

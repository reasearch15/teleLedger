from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.base import BaseRepository
from app.models.user import User, UserRole


class UserRepository(BaseRepository[User]):
    """Persistence operations for local user accounts."""

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def add(self, user: User) -> User:
        """Stage a user and load database-generated fields."""
        self._session.add(user)
        await self._session.flush()
        return user

    async def get_by_id(self, user_id: int) -> User | None:
        """Find an account by its internal ID."""
        result = await self._session.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    async def get_by_username(self, username: str) -> User | None:
        """Find an account by its normalized username."""
        result = await self._session.execute(
            select(User).where(User.username == username)
        )
        return result.scalar_one_or_none()

    async def list_staff(self) -> list[User]:
        """List staff accounts in stable username order."""
        result = await self._session.scalars(
            select(User)
            .where(User.role == UserRole.STAFF)
            .order_by(User.username.asc())
        )
        return list(result)

    async def list_coadmins(self) -> list[User]:
        """List coadmin accounts in stable username order."""
        result = await self._session.scalars(
            select(User)
            .where(User.role == UserRole.COADMIN)
            .order_by(User.username.asc())
        )
        return list(result)

    async def get_staff_for_update(self, user_id: int) -> User | None:
        """Lock one staff account for an administrative mutation."""
        result = await self._session.execute(
            select(User)
            .where(User.id == user_id, User.role == UserRole.STAFF)
            .with_for_update()
        )
        return result.scalar_one_or_none()

    async def get_active_staff(self, user_id: int) -> User | None:
        """Return an active staff account suitable for assignment."""
        result = await self._session.execute(
            select(User).where(
                User.id == user_id,
                User.role == UserRole.STAFF,
                User.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def get_active_coadmin(self, user_id: int) -> User | None:
        """Return an active coadmin suitable for staff ownership."""
        result = await self._session.execute(
            select(User).where(
                User.id == user_id,
                User.role == UserRole.COADMIN,
                User.is_active.is_(True),
            )
        )
        return result.scalar_one_or_none()

    async def flush(self, user: User) -> None:
        """Flush a mutation and reload its database-managed update time."""
        await self._session.flush()
        await self._session.refresh(user, attribute_names=["updated_at"])

    async def delete(self, user: User) -> None:
        """Remove one user row."""
        await self._session.delete(user)
        await self._session.flush()

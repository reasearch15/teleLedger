from __future__ import annotations

from collections.abc import Awaitable, Callable

from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.db.session import SessionFactory

type ReadOperation[T] = Callable[[AsyncSession], Awaitable[T]]
logger = get_logger(__name__)

_DISCONNECT_EXCEPTION_NAMES = {
    "ConnectionDoesNotExistError",
    "ConnectionResetError",
}
_DISCONNECT_MESSAGES = (
    "connection was closed in the middle of operation",
    "connection is closed",
    "closed the connection unexpectedly",
    "server closed the connection",
)


def is_transient_disconnect(error: BaseException) -> bool:
    """Recognize stale/closed PostgreSQL connections through wrapper chains."""
    pending: list[BaseException] = [error]
    seen: set[int] = set()

    while pending:
        current = pending.pop()
        if id(current) in seen:
            continue
        seen.add(id(current))

        if isinstance(current, DBAPIError) and current.connection_invalidated:
            return True
        if type(current).__name__ in _DISCONNECT_EXCEPTION_NAMES:
            return True
        if isinstance(current, OSError) and getattr(current, "winerror", None) == 10054:
            return True
        if any(message in str(current).lower() for message in _DISCONNECT_MESSAGES):
            return True

        if isinstance(current, DBAPIError) and isinstance(current.orig, BaseException):
            pending.append(current.orig)
        if current.__cause__ is not None:
            pending.append(current.__cause__)
        if current.__context__ is not None:
            pending.append(current.__context__)

    return False


async def run_read_with_retry[T](
    operation: ReadOperation[T],
    *,
    session: AsyncSession,
    operation_name: str,
) -> T:
    """Run an idempotent read and retry one disconnect with a fresh session."""
    try:
        return await operation(session)
    except Exception as error:
        if not is_transient_disconnect(error):
            raise

        logger.warning(
            "stale_database_connection_detected",
            extra={"database_operation": operation_name},
        )
        await _discard_session(session)

        try:
            async with SessionFactory() as retry_session:
                result = await operation(retry_session)
        except Exception:
            logger.exception(
                "database_read_retry_failed",
                extra={"database_operation": operation_name},
            )
            raise

        logger.info(
            "database_read_retry_succeeded",
            extra={"database_operation": operation_name},
        )
        return result


async def _discard_session(session: AsyncSession) -> None:
    """Best-effort cleanup of the session associated with a dead connection."""
    try:
        await session.rollback()
    except Exception:
        logger.debug("stale_database_session_rollback_failed", exc_info=True)
    try:
        await session.close()
    except Exception:
        logger.debug("stale_database_session_close_failed", exc_info=True)

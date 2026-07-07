from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth.security import InvalidSessionTokenError, decode_session_token
from app.core.config import get_settings
from app.db.retry import run_read_with_retry
from app.db.session import get_session
from app.models.user import User, UserRole
from app.services.cashout import CashoutService
from app.services.payment import PaymentService
from app.services.user import (
    AuthService,
    InvalidCredentialsError,
    StaffManagementService,
)

DatabaseSession = Annotated[AsyncSession, Depends(get_session)]
AuthDatabaseSession = Annotated[AsyncSession, Depends(get_session)]
FunctionAuthDatabaseSession = Annotated[
    AsyncSession,
    Depends(get_session, scope="function"),
]
settings = get_settings()


def get_payment_service(session: DatabaseSession) -> PaymentService:
    """Build a request-scoped payment service."""
    return PaymentService(session)


PaymentServiceDependency = Annotated[PaymentService, Depends(get_payment_service)]


def get_cashout_service(session: DatabaseSession) -> CashoutService:
    """Build a request-scoped cashout service."""
    return CashoutService(session)


CashoutServiceDependency = Annotated[CashoutService, Depends(get_cashout_service)]


def get_auth_service(session: DatabaseSession) -> AuthService:
    """Build a request-scoped authentication service."""
    return AuthService(session)


def get_staff_management_service(session: DatabaseSession) -> StaffManagementService:
    """Build a request-scoped staff management service."""
    return StaffManagementService(session)


AuthServiceDependency = Annotated[AuthService, Depends(get_auth_service)]
StaffManagementServiceDependency = Annotated[
    StaffManagementService,
    Depends(get_staff_management_service),
]


async def get_current_user(request: Request, session: AuthDatabaseSession) -> User:
    """Authenticate the HTTP-only session cookie against current account state."""
    return await _authenticate_request(request, session)


async def get_stream_current_user(
    request: Request,
    session: FunctionAuthDatabaseSession,
) -> User:
    """Authenticate a stream, closing its DB session before streaming starts."""
    return await _authenticate_request(request, session)


async def _authenticate_request(request: Request, session: AsyncSession) -> User:
    token = request.cookies.get(settings.auth_cookie_name)
    if token is None:
        raise _unauthorized()

    try:
        user_id = decode_session_token(
            token,
            settings.auth_secret_key.get_secret_value(),
        )
        user = await run_read_with_retry(
            lambda read_session: _get_active_user(read_session, user_id),
            session=session,
            operation_name="auth.me",
        )
        return user
    except (InvalidSessionTokenError, InvalidCredentialsError) as error:
        raise _unauthorized() from error


async def _get_active_user(session: AsyncSession, user_id: int) -> User:
    user = await AuthService(session).get_active_user(user_id)
    # Release the connection immediately; the returned model remains usable
    # because request sessions do not expire loaded attributes on commit.
    await session.commit()
    return user


def require_admin(current_user: Annotated[User, Depends(get_current_user)]) -> User:
    """Require an authenticated administrator."""
    if current_user.role != UserRole.ADMIN:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Administrator access required",
        )
    return current_user


def _unauthorized() -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Authentication required",
    )


CurrentUser = Annotated[User, Depends(get_current_user)]
AdminUser = Annotated[User, Depends(require_admin)]

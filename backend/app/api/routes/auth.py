from fastapi import APIRouter, HTTPException, Response, status

from app.api.dependencies import AuthServiceDependency, CurrentUser
from app.auth.security import create_session_token
from app.core.config import get_settings
from app.schemas.auth import LoginRequest, UserResponse
from app.services.user import InvalidCredentialsError

router = APIRouter(prefix="/api/auth", tags=["authentication"])
settings = get_settings()


@router.post("/login", response_model=UserResponse)
async def login(
    credentials: LoginRequest,
    response: Response,
    service: AuthServiceDependency,
) -> UserResponse:
    """Authenticate a local user and set an HTTP-only session cookie."""
    try:
        user = await service.authenticate(
            credentials.username,
            credentials.password.get_secret_value(),
        )
    except InvalidCredentialsError as error:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
        ) from error

    token = create_session_token(
        user.id,
        settings.auth_secret_key.get_secret_value(),
        settings.auth_session_minutes,
    )
    response.set_cookie(
        key=settings.auth_cookie_name,
        value=token,
        max_age=settings.auth_session_minutes * 60,
        httponly=True,
        secure=settings.auth_cookie_secure,
        samesite="lax",
        path="/",
    )
    return UserResponse.model_validate(user)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    """Remove the local session cookie."""
    response.delete_cookie(
        key=settings.auth_cookie_name,
        path="/",
        secure=settings.auth_cookie_secure,
        httponly=True,
        samesite="lax",
    )


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: CurrentUser) -> UserResponse:
    """Return the currently authenticated active user."""
    return UserResponse.model_validate(current_user)


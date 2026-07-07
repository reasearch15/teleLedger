from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status

from app.api.dependencies import AdminUser, StaffManagementServiceDependency
from app.schemas.auth import CreateStaffRequest, ResetPasswordRequest, UserResponse
from app.services.user import (
    StaffNotFoundError,
    StaffSelfDeleteForbiddenError,
    UsernameAlreadyExistsError,
)

router = APIRouter(prefix="/api/admin/staff", tags=["staff management"])
StaffId = Annotated[int, Path(gt=0)]


@router.get("", response_model=list[UserResponse])
async def list_staff(
    _: AdminUser,
    service: StaffManagementServiceDependency,
) -> list[UserResponse]:
    """List all staff accounts."""
    users = await service.list_staff()
    return [UserResponse.model_validate(user) for user in users]


@router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_staff(
    request: CreateStaffRequest,
    _: AdminUser,
    service: StaffManagementServiceDependency,
) -> UserResponse:
    """Create an active staff account."""
    try:
        user = await service.create_staff(
            request.username,
            request.password.get_secret_value(),
        )
    except UsernameAlreadyExistsError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    return UserResponse.model_validate(user)


@router.patch("/{staff_id}/disable", response_model=UserResponse)
async def disable_staff(
    staff_id: StaffId,
    _: AdminUser,
    service: StaffManagementServiceDependency,
) -> UserResponse:
    """Disable a staff account."""
    try:
        user = await service.disable_staff(staff_id)
    except StaffNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    return UserResponse.model_validate(user)


@router.patch("/{staff_id}/reset-password", response_model=UserResponse)
async def reset_staff_password(
    staff_id: StaffId,
    request: ResetPasswordRequest,
    _: AdminUser,
    service: StaffManagementServiceDependency,
) -> UserResponse:
    """Replace a staff account password."""
    try:
        user = await service.reset_password(
            staff_id,
            request.password.get_secret_value(),
        )
    except StaffNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    return UserResponse.model_validate(user)


@router.delete("/{staff_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_staff(
    staff_id: StaffId,
    current_user: AdminUser,
    service: StaffManagementServiceDependency,
) -> None:
    """Permanently delete a staff account and detach historical references."""
    try:
        await service.delete_staff(staff_id, actor=current_user)
    except StaffNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except StaffSelfDeleteForbiddenError as error:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=str(error),
        ) from error


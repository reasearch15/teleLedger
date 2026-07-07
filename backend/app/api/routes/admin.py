from typing import Annotated

from fastapi import APIRouter, HTTPException, Path, status

from app.api.dependencies import AdminUser, StaffManagementServiceDependency
from app.models.user import User
from app.schemas.auth import (
    AssignStaffCoadminRequest,
    CreateCoadminRequest,
    CreateStaffRequest,
    ResetPasswordRequest,
    UserResponse,
)
from app.services.user import (
    CoadminHasAssignedStaffError,
    CoadminNotFoundError,
    StaffNotFoundError,
    StaffSelfDeleteForbiddenError,
    UsernameAlreadyExistsError,
)

router = APIRouter(prefix="/api/admin/staff", tags=["staff management"])
coadmin_router = APIRouter(prefix="/api/admin/coadmins", tags=["coadmin management"])
StaffId = Annotated[int, Path(gt=0)]
CoadminId = Annotated[int, Path(gt=0)]


@router.get("", response_model=list[UserResponse])
async def list_staff(
    _: AdminUser,
    service: StaffManagementServiceDependency,
) -> list[UserResponse]:
    """List all staff accounts."""
    users = await service.list_staff()
    coadmins = {coadmin.id: coadmin.username for coadmin in await service.list_coadmins()}
    return [_serialize_user(user, coadmins=coadmins) for user in users]


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
            coadmin_id=request.coadmin_id,
        )
    except UsernameAlreadyExistsError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    except CoadminNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    coadmins = {coadmin.id: coadmin.username for coadmin in await service.list_coadmins()}
    return _serialize_user(user, coadmins=coadmins)


@router.patch("/{staff_id}/coadmin", response_model=UserResponse)
async def assign_staff_coadmin(
    staff_id: StaffId,
    request: AssignStaffCoadminRequest,
    _: AdminUser,
    service: StaffManagementServiceDependency,
) -> UserResponse:
    """Assign an existing staff account to an active coadmin."""
    try:
        user = await service.assign_staff_coadmin(staff_id, request.coadmin_id)
    except StaffNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except CoadminNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    coadmins = {coadmin.id: coadmin.username for coadmin in await service.list_coadmins()}
    return _serialize_user(user, coadmins=coadmins)


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
    coadmins = {coadmin.id: coadmin.username for coadmin in await service.list_coadmins()}
    return _serialize_user(user, coadmins=coadmins)


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
    coadmins = {coadmin.id: coadmin.username for coadmin in await service.list_coadmins()}
    return _serialize_user(user, coadmins=coadmins)


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


@coadmin_router.get("", response_model=list[UserResponse])
async def list_coadmins(
    _: AdminUser,
    service: StaffManagementServiceDependency,
) -> list[UserResponse]:
    """List all coadmin accounts."""
    users = await service.list_coadmins()
    return [UserResponse.model_validate(user) for user in users]


@coadmin_router.post("", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def create_coadmin(
    request: CreateCoadminRequest,
    _: AdminUser,
    service: StaffManagementServiceDependency,
) -> UserResponse:
    """Create a coadmin account."""
    try:
        user = await service.create_coadmin(
            request.username,
            request.password.get_secret_value(),
            is_active=request.is_active,
        )
    except UsernameAlreadyExistsError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error
    return UserResponse.model_validate(user)


@coadmin_router.patch("/{coadmin_id}/reset-password", response_model=UserResponse)
async def reset_coadmin_password(
    coadmin_id: CoadminId,
    request: ResetPasswordRequest,
    _: AdminUser,
    service: StaffManagementServiceDependency,
) -> UserResponse:
    """Replace a coadmin account password."""
    try:
        user = await service.reset_coadmin_password(
            coadmin_id,
            request.password.get_secret_value(),
        )
    except CoadminNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    return UserResponse.model_validate(user)


@coadmin_router.patch("/{coadmin_id}/disable", response_model=UserResponse)
async def disable_coadmin(
    coadmin_id: CoadminId,
    _: AdminUser,
    service: StaffManagementServiceDependency,
) -> UserResponse:
    """Disable a coadmin account."""
    try:
        user = await service.disable_coadmin(coadmin_id)
    except CoadminNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    return UserResponse.model_validate(user)


@coadmin_router.patch("/{coadmin_id}/activate", response_model=UserResponse)
async def activate_coadmin(
    coadmin_id: CoadminId,
    _: AdminUser,
    service: StaffManagementServiceDependency,
) -> UserResponse:
    """Reactivate a disabled coadmin account."""
    try:
        user = await service.activate_coadmin(coadmin_id)
    except CoadminNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    return UserResponse.model_validate(user)


@coadmin_router.delete("/{coadmin_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_coadmin(
    coadmin_id: CoadminId,
    _: AdminUser,
    service: StaffManagementServiceDependency,
) -> None:
    """Permanently delete a coadmin account when no staff remain assigned."""
    try:
        await service.delete_coadmin(coadmin_id)
    except CoadminNotFoundError as error:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=str(error),
        ) from error
    except CoadminHasAssignedStaffError as error:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(error),
        ) from error


def _serialize_user(
    user: User,
    *,
    coadmins: dict[int, str],
) -> UserResponse:
    coadmin_username = (
        coadmins.get(user.coadmin_id) if user.coadmin_id is not None else None
    )
    return UserResponse.model_validate(user).model_copy(
        update={"coadmin_username": coadmin_username}
    )

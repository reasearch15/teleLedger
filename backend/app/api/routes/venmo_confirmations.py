from __future__ import annotations

import asyncio
import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Annotated, Any
from uuid import uuid4

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status
from fastapi import Path as ApiPath
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict
from sqlalchemy import select

from app.api.dependencies import CurrentUser, DatabaseSession
from app.core.config import get_settings
from app.models.media_asset import MediaAsset
from app.models.user import User
from app.models.venmo_confirmation import (
    VenmoConfirmationAttempt,
    VenmoConfirmationAttemptStatus,
    VenmoConfirmationEvent,
    VenmoConfirmationEventType,
    VenmoConfirmationInquiry,
    VenmoConfirmationInquiryStatus,
    VenmoConfirmationRequest,
    VenmoConfirmationStatus,
)
from app.services.venmo_confirmation import (
    VenmoConfirmationAuthorizationError,
    VenmoConfirmationNotFoundError,
    VenmoConfirmationService,
    VenmoConfirmationStateConflictError,
)
from app.telegram.inquiry_media import ALLOWED_IMAGE_MIME_TYPES

router = APIRouter(prefix="/api/venmo-confirmations", tags=["venmo-confirmations"])

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")


class MediaAssetResponse(BaseModel):
    id: int
    original_filename: str | None
    mime_type: str
    size_bytes: int
    created_at: datetime
    preview_url: str


class VenmoAttemptResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_id: int
    attempt_number: int
    telegram_chat_id: int | None
    telegram_message_id: int | None
    status: VenmoConfirmationAttemptStatus
    created_at: datetime
    posted_at: datetime | None
    resolved_at: datetime | None
    last_error: str | None


class VenmoInquiryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_id: int
    source_attempt_id: int
    resulting_attempt_id: int | None
    status: VenmoConfirmationInquiryStatus
    created_at: datetime
    dismissed_at: datetime | None
    dismissed_by_staff_id: int | None
    resent_at: datetime | None
    resent_by_staff_id: int | None


class VenmoEventResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    request_id: int
    attempt_id: int | None
    inquiry_id: int | None
    event_type: VenmoConfirmationEventType
    actor_user_id: int | None
    actor_username: str | None = None
    actor_source: str | None
    actor_identifier: str | None
    payload: dict[str, Any] | None
    created_at: datetime


class VenmoRequestSummaryResponse(BaseModel):
    id: int
    coadmin_id: int
    requested_by_staff_id: int | None
    requested_by_username: str | None
    coadmin_username: str | None
    screenshot_media_asset_id: int
    status: VenmoConfirmationStatus
    payment_note: str | None
    metadata: dict[str, Any] | None
    confirmed_at: datetime | None
    confirmed_by_display_name: str | None
    created_at: datetime
    updated_at: datetime
    media: MediaAssetResponse | None = None


class VenmoRequestDetailResponse(VenmoRequestSummaryResponse):
    attempts: list[VenmoAttemptResponse]
    inquiries: list[VenmoInquiryResponse]
    events: list[VenmoEventResponse]


class VenmoRequestListResponse(BaseModel):
    items: list[VenmoRequestSummaryResponse]


@router.get("", response_model=VenmoRequestListResponse)
async def list_venmo_confirmations(
    session: DatabaseSession,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> VenmoRequestListResponse:
    service = VenmoConfirmationService(session)
    try:
        requests = await service.list_requests_for_actor(
            actor=current_user,
            limit=limit,
            offset=offset,
        )
    except VenmoConfirmationAuthorizationError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    usernames = await _usernames(session, requests)
    return VenmoRequestListResponse(
        items=[_serialize_summary(request, usernames=usernames) for request in requests]
    )


@router.post("/attempts/{attempt_id}/confirm", response_model=VenmoRequestDetailResponse)
async def confirm_venmo_attempt(
    attempt_id: Annotated[int, ApiPath(gt=0)],
    session: DatabaseSession,
    current_user: CurrentUser,
) -> VenmoRequestDetailResponse:
    service = VenmoConfirmationService(session)
    try:
        attempt = await service._repository.get_attempt_for_coadmin(  # noqa: SLF001
            attempt_id,
            service._actor_coadmin_id(current_user),  # noqa: SLF001
        )
        if attempt is None:
            raise VenmoConfirmationNotFoundError("Venmo confirmation attempt was not found.")
        await service.mark_confirmed(
            attempt_id=attempt_id,
            coadmin_id=service._actor_coadmin_id(current_user),  # noqa: SLF001
            display_name=current_user.username,
        )
        await session.commit()
        return await get_venmo_confirmation(attempt.request_id, session, current_user)
    except Exception as error:
        _raise_venmo_error(error)


@router.post("/attempts/{attempt_id}/not-received", response_model=VenmoRequestDetailResponse)
async def mark_venmo_attempt_not_received(
    attempt_id: Annotated[int, ApiPath(gt=0)],
    session: DatabaseSession,
    current_user: CurrentUser,
) -> VenmoRequestDetailResponse:
    service = VenmoConfirmationService(session)
    try:
        coadmin_id = service._actor_coadmin_id(current_user)  # noqa: SLF001
        attempt = await service._repository.get_attempt_for_coadmin(  # noqa: SLF001
            attempt_id,
            coadmin_id,
        )
        if attempt is None:
            raise VenmoConfirmationNotFoundError("Venmo confirmation attempt was not found.")
        await service.mark_attempt_not_received(attempt_id=attempt_id, coadmin_id=coadmin_id)
        await session.commit()
        return await get_venmo_confirmation(attempt.request_id, session, current_user)
    except Exception as error:
        _raise_venmo_error(error)


@router.post("/inquiries/{inquiry_id}/dismiss", response_model=VenmoRequestDetailResponse)
async def dismiss_venmo_inquiry(
    inquiry_id: Annotated[int, ApiPath(gt=0)],
    session: DatabaseSession,
    current_user: CurrentUser,
) -> VenmoRequestDetailResponse:
    service = VenmoConfirmationService(session)
    try:
        coadmin_id = service._actor_coadmin_id(current_user)  # noqa: SLF001
        inquiry = await service.dismiss_inquiry(
            inquiry_id=inquiry_id,
            coadmin_id=coadmin_id,
            actor=current_user,
        )
        await session.commit()
        return await get_venmo_confirmation(inquiry.request_id, session, current_user)
    except Exception as error:
        _raise_venmo_error(error)


@router.post("/{request_id}/resend", response_model=VenmoRequestDetailResponse)
async def resend_venmo_confirmation(
    request_id: Annotated[int, ApiPath(gt=0)],
    session: DatabaseSession,
    current_user: CurrentUser,
) -> VenmoRequestDetailResponse:
    service = VenmoConfirmationService(session)
    try:
        request = await service.get_request_for_actor(request_id, actor=current_user)
        attempt = await service.create_attempt(
            request_id=request.id,
            coadmin_id=request.coadmin_id,
        )
        await service._record_event(  # noqa: SLF001
            request_id=request.id,
            attempt_id=attempt.id,
            event_type=VenmoConfirmationEventType.RESEND_REQUESTED,
            actor=current_user,
        )
        await session.commit()
        return await get_venmo_confirmation(request.id, session, current_user)
    except Exception as error:
        _raise_venmo_error(error)


@router.post("/{request_id}/payment-screenshot", response_model=VenmoRequestDetailResponse)
async def upload_venmo_payment_screenshot(
    request_id: Annotated[int, ApiPath(gt=0)],
    session: DatabaseSession,
    current_user: CurrentUser,
    file: Annotated[UploadFile, File()],
) -> VenmoRequestDetailResponse:
    service = VenmoConfirmationService(session)
    try:
        request = await service.get_request_for_actor(request_id, actor=current_user)
        upload = await _prepare_payment_screenshot_upload(
            file,
            coadmin_id=request.coadmin_id,
            request_id=request.id,
        )
        await asyncio.to_thread(_write_upload, upload.storage_key, upload.content)
        await service.replace_payment_screenshot(
            request_id=request.id,
            actor=current_user,
            storage_key=upload.storage_key,
            original_filename=upload.original_filename,
            mime_type=upload.mime_type,
            size_bytes=len(upload.content),
            checksum_sha256=hashlib.sha256(upload.content).hexdigest(),
        )
        await session.commit()
        return await get_venmo_confirmation(request.id, session, current_user)
    except Exception as error:
        _raise_venmo_error(error)


@router.get("/media/{media_id}")
async def get_venmo_media(
    media_id: Annotated[int, ApiPath(gt=0)],
    session: DatabaseSession,
    current_user: CurrentUser,
) -> FileResponse:
    try:
        media = await VenmoConfirmationService(session).get_media_for_actor(
            media_id,
            actor=current_user,
        )
    except VenmoConfirmationAuthorizationError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except VenmoConfirmationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    path = _media_path(media.storage_key)
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media file is missing",
        )
    return FileResponse(
        path=path,
        media_type=media.mime_type,
        filename=media.original_filename or path.name,
    )


@router.get("/{request_id}", response_model=VenmoRequestDetailResponse)
async def get_venmo_confirmation(
    request_id: Annotated[int, ApiPath(gt=0)],
    session: DatabaseSession,
    current_user: CurrentUser,
) -> VenmoRequestDetailResponse:
    service = VenmoConfirmationService(session)
    try:
        request, media, attempts, inquiries, events = await service.get_detail_for_actor(
            request_id,
            actor=current_user,
        )
    except VenmoConfirmationAuthorizationError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except VenmoConfirmationNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    usernames = await _usernames(session, [request])
    event_usernames = await _event_usernames(session, events)
    return _serialize_detail(
        request,
        media=media,
        attempts=attempts,
        inquiries=inquiries,
        events=events,
        usernames=usernames,
        event_usernames=event_usernames,
    )


async def _usernames(
    session: DatabaseSession,
    requests: list[VenmoConfirmationRequest],
) -> dict[int, str]:
    user_ids = {
        user_id
        for request in requests
        for user_id in (request.requested_by_staff_id, request.coadmin_id)
        if user_id is not None
    }
    if not user_ids:
        return {}
    rows = await session.execute(select(User.id, User.username).where(User.id.in_(user_ids)))
    return {int(row[0]): str(row[1]) for row in rows}


async def _event_usernames(
    session: DatabaseSession,
    events: list[VenmoConfirmationEvent],
) -> dict[int, str]:
    user_ids = {event.actor_user_id for event in events if event.actor_user_id is not None}
    if not user_ids:
        return {}
    rows = await session.execute(select(User.id, User.username).where(User.id.in_(user_ids)))
    return {int(row[0]): str(row[1]) for row in rows}


def _serialize_summary(
    request: VenmoConfirmationRequest,
    *,
    usernames: dict[int, str],
    media: MediaAsset | None = None,
) -> VenmoRequestSummaryResponse:
    return VenmoRequestSummaryResponse(
        id=request.id,
        coadmin_id=request.coadmin_id,
        requested_by_staff_id=request.requested_by_staff_id,
        requested_by_username=(
            usernames.get(request.requested_by_staff_id)
            if request.requested_by_staff_id is not None
            else None
        ),
        coadmin_username=usernames.get(request.coadmin_id),
        screenshot_media_asset_id=request.screenshot_media_asset_id,
        status=request.status,
        payment_note=request.payment_note,
        metadata=request.metadata_json,
        confirmed_at=request.confirmed_at,
        confirmed_by_display_name=request.confirmed_by_display_name,
        created_at=request.created_at,
        updated_at=request.updated_at,
        media=_serialize_media(media) if media is not None else None,
    )


def _serialize_detail(
    request: VenmoConfirmationRequest,
    *,
    media: MediaAsset,
    attempts: list[VenmoConfirmationAttempt],
    inquiries: list[VenmoConfirmationInquiry],
    events: list[VenmoConfirmationEvent],
    usernames: dict[int, str],
    event_usernames: dict[int, str],
) -> VenmoRequestDetailResponse:
    return VenmoRequestDetailResponse(
        **_serialize_summary(request, usernames=usernames, media=media).model_dump(),
        attempts=[VenmoAttemptResponse.model_validate(attempt) for attempt in attempts],
        inquiries=[VenmoInquiryResponse.model_validate(inquiry) for inquiry in inquiries],
        events=[
            VenmoEventResponse.model_validate(event).model_copy(
                update={
                    "actor_username": (
                        event_usernames.get(event.actor_user_id)
                        if event.actor_user_id is not None
                        else None
                    )
                }
            )
            for event in events
        ],
    )


def _serialize_media(media: MediaAsset) -> MediaAssetResponse:
    return MediaAssetResponse(
        id=media.id,
        original_filename=media.original_filename,
        mime_type=media.mime_type,
        size_bytes=media.size_bytes,
        created_at=media.created_at,
        preview_url=f"/api/venmo-confirmations/media/{media.id}",
    )


def _media_path(storage_key: str) -> Path:
    root = Path(get_settings().inquiry_media_dir).resolve()
    path = (root / storage_key).resolve()
    if not path.is_relative_to(root):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media is not available",
        )
    return path


@dataclass(frozen=True, slots=True)
class _PreparedUpload:
    content: bytes
    storage_key: str
    original_filename: str
    mime_type: str


async def _prepare_payment_screenshot_upload(
    file: UploadFile,
    *,
    coadmin_id: int,
    request_id: int,
) -> _PreparedUpload:
    settings = get_settings()
    max_bytes = settings.inquiry_media_max_bytes
    mime_type = file.content_type or ""
    if mime_type not in ALLOWED_IMAGE_MIME_TYPES:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only JPEG, PNG, and WEBP images are supported",
        )
    content = await file.read(max_bytes + 1)
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Uploaded image is empty",
        )
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_CONTENT_TOO_LARGE,
            detail="Uploaded image is too large",
        )
    if not _content_matches_image_type(content, mime_type):
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Uploaded file content does not match a supported image type",
        )
    original_filename = _safe_original_filename(file.filename)
    storage_key = f"venmo/{coadmin_id}/{request_id}/{uuid4().hex}-{original_filename}"
    return _PreparedUpload(
        content=content,
        storage_key=storage_key,
        original_filename=original_filename,
        mime_type=mime_type,
    )


def _safe_original_filename(filename: str | None) -> str:
    basename = Path(filename or "payment-screenshot").name
    sanitized = _SAFE_FILENAME_RE.sub("_", basename).strip("._")
    if not sanitized:
        return "payment-screenshot"
    return sanitized[:255]


def _content_matches_image_type(content: bytes, mime_type: str) -> bool:
    if mime_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if mime_type == "image/webp":
        return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    return False


def _write_upload(storage_key: str, content: bytes) -> None:
    path = _media_path(storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def _raise_venmo_error(error: Exception) -> None:
    if isinstance(error, HTTPException):
        raise error
    if isinstance(error, VenmoConfirmationNotFoundError):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    if isinstance(error, VenmoConfirmationAuthorizationError):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    if isinstance(error, VenmoConfirmationStateConflictError):
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(error)) from error
    raise error

from typing import Annotated

from fastapi import APIRouter, File, Form, HTTPException, Path, Query, UploadFile, status
from fastapi.responses import FileResponse

from app.api.dependencies import CurrentUser, DatabaseSession
from app.core.config import get_settings
from app.db.retry import run_read_with_retry
from app.models.inquiry_message import InquiryMessage
from app.schemas.inquiry import (
    InquiryMessageListResponse,
    InquiryMessageResponse,
    SendInquiryMessageResponse,
)
from app.services.inquiry import (
    InquiryAuthorizationError,
    InquiryNotFoundError,
    InquiryService,
    InquiryValidationError,
)
from app.telegram.inquiry_media import media_path_for_key

router = APIRouter(prefix="/api/inquiries", tags=["inquiries"])


@router.get("/messages", response_model=InquiryMessageListResponse)
async def list_inquiry_messages(
    session: DatabaseSession,
    current_user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=100)] | None = None,
    cursor: Annotated[str | None, Query(max_length=128)] = None,
) -> InquiryMessageListResponse:
    """List visible cashout-group chat messages for the Inquiry panel."""
    settings = get_settings()
    page_limit = limit or settings.inquiry_page_size_default
    try:
        messages, pagination, usernames = await run_read_with_retry(
            lambda read_session: InquiryService(read_session).list_messages(
                actor=current_user,
                limit=page_limit,
                cursor=cursor,
            ),
            session=session,
            operation_name="inquiries.list_messages",
        )
        block_flags = await run_read_with_retry(
            lambda read_session: InquiryService(read_session).compute_sender_block_flags(
                messages
            ),
            session=session,
            operation_name="inquiries.grouping",
        )
    except InquiryAuthorizationError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except InquiryValidationError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

    return InquiryMessageListResponse(
        items=[
            _serialize_message(
                message,
                starts_new_sender_block=block_flags.get(message.id, True),
                sent_by_username=usernames.get(message.sent_by_teleledger_user_id or -1),
            )
            for message in messages
        ],
        pagination=pagination,
    )


@router.post("/send", response_model=SendInquiryMessageResponse)
async def send_inquiry_message(
    session: DatabaseSession,
    current_user: CurrentUser,
    idempotency_key: Annotated[str, Form()],
    text: Annotated[str | None, Form(max_length=4000)] = None,
    image: UploadFile | None = File(default=None),
) -> SendInquiryMessageResponse:
    """Send one text or image message into the cashout Telegram group."""
    from uuid import UUID

    try:
        key = UUID(idempotency_key)
    except ValueError as error:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="idempotency_key must be a UUID",
        ) from error

    service = InquiryService(session)
    try:
        message = await service.send_message(
            actor=current_user,
            text=text,
            idempotency_key=key,
            image=image,
        )
        await session.commit()
    except InquiryAuthorizationError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except InquiryValidationError as error:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(error)) from error

    return SendInquiryMessageResponse(
        message=_serialize_message(message, starts_new_sender_block=True),
    )


@router.get("/messages/{message_id}/media")
async def get_inquiry_message_media(
    session: DatabaseSession,
    current_user: CurrentUser,
    message_id: Annotated[int, Path(gt=0)],
) -> FileResponse:
    """Serve one cached inquiry image through an authenticated route."""
    settings = get_settings()
    service = InquiryService(session)
    try:
        message = await service.get_message_for_media(
            actor=current_user,
            message_id=message_id,
        )
    except InquiryAuthorizationError as error:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(error)) from error
    except InquiryNotFoundError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error

    if not message.media_storage_key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media is not available",
        )
    try:
        media_path = media_path_for_key(settings, message.media_storage_key)
    except ValueError as error:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(error)) from error
    if not media_path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Media file is missing",
        )
    return FileResponse(
        path=media_path,
        media_type=message.media_mime_type or "application/octet-stream",
        filename=message.media_filename or media_path.name,
    )


def _serialize_message(
    message: InquiryMessage,
    *,
    starts_new_sender_block: bool,
    sent_by_username: str | None = None,
) -> InquiryMessageResponse:
    return InquiryMessageResponse(
        id=message.id,
        telegram_chat_id=message.telegram_chat_id,
        telegram_message_id=message.telegram_message_id,
        telegram_sender_id=message.telegram_sender_id,
        sender_display_name=message.sender_display_name,
        sender_username=message.sender_username,
        text=message.text,
        caption=message.caption,
        message_date=message.message_date,
        edited_at=message.edited_at,
        direction=message.direction.value,
        message_source=message.message_source.value,
        media_type=message.media_type.value,
        media_mime_type=message.media_mime_type,
        media_filename=message.media_filename,
        media_size_bytes=message.media_size_bytes,
        media_download_status=message.media_download_status.value,
        media_error=message.media_error,
        has_media=message.media_type.value != "none",
        telegram_grouped_id=message.telegram_grouped_id,
        reply_to_telegram_message_id=message.reply_to_telegram_message_id,
        forward_from_display_name=message.forward_from_display_name,
        is_deleted=message.is_deleted,
        sent_by_teleledger_user_id=message.sent_by_teleledger_user_id,
        sent_by_username=sent_by_username,
        starts_new_sender_block=starts_new_sender_block,
        is_edited=message.edited_at is not None,
    )

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from fastapi import HTTPException, UploadFile, status

from app.core.config import get_settings
from app.telegram.inquiry_media import ALLOWED_IMAGE_MIME_TYPES

_SAFE_FILENAME_RE = re.compile(r"[^A-Za-z0-9._-]+")
QR_PLAYER_TAG = "QR"


@dataclass(frozen=True, slots=True)
class PreparedQrCashoutUpload:
    content: bytes
    storage_key: str
    original_filename: str
    mime_type: str
    checksum_sha256: str


async def prepare_qr_image_upload(
    file: UploadFile,
    *,
    coadmin_id: int,
) -> PreparedQrCashoutUpload:
    """Validate and prepare one QR image upload for durable cash-out storage."""
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
    storage_key = f"cashout/{coadmin_id}/qr/{uuid4().hex}-{original_filename}"
    return PreparedQrCashoutUpload(
        content=content,
        storage_key=storage_key,
        original_filename=original_filename,
        mime_type=mime_type,
        checksum_sha256=hashlib.sha256(content).hexdigest(),
    )


def write_cashout_media(storage_key: str, content: bytes) -> None:
    path = resolve_cashout_media_path(storage_key)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)


def resolve_cashout_media_path(storage_key: str) -> Path:
    root = Path(get_settings().inquiry_media_dir).resolve()
    if ".." in storage_key or storage_key.startswith(("/", "\\")):
        raise ValueError("Invalid cashout media storage key")
    path = (root / storage_key).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Invalid cashout media storage key")
    return path


def _safe_original_filename(filename: str | None) -> str:
    basename = Path(filename or "qr-code").name
    sanitized = _SAFE_FILENAME_RE.sub("_", basename).strip("._")
    if not sanitized:
        return "qr-code"
    return sanitized[:255]


def _content_matches_image_type(content: bytes, mime_type: str) -> bool:
    if mime_type == "image/png":
        return content.startswith(b"\x89PNG\r\n\x1a\n")
    if mime_type == "image/jpeg":
        return content.startswith(b"\xff\xd8\xff")
    if mime_type == "image/webp":
        return len(content) >= 12 and content[:4] == b"RIFF" and content[8:12] == b"WEBP"
    return False

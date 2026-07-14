from __future__ import annotations

import asyncio
import re
from pathlib import Path

from app.core.config import Settings

ALLOWED_IMAGE_MIME_TYPES = frozenset(
    {
        "image/jpeg",
        "image/png",
        "image/webp",
    }
)
MIME_EXTENSION = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
}
EXTENSION_MIME = {
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}
ALLOWED_STORAGE_EXTENSIONS = frozenset({".jpg", ".png", ".webp"})
CHAT_COMPONENT_PATTERN = re.compile(r"^chat_-?\d+$")
MESSAGE_FILE_PATTERN = re.compile(r"^\d+\.(jpg|png|webp)$", re.IGNORECASE)


class InvalidInquiryMediaStorageKeyError(ValueError):
    """Raised when a storage key fails validation."""


def resolve_inquiry_media_root(settings: Settings) -> Path:
    """Return the configured inquiry media directory, creating it when needed."""
    root = Path(settings.inquiry_media_dir)
    if not root.is_absolute():
        backend_root = Path(__file__).resolve().parents[2]
        root = backend_root / root
    root.mkdir(parents=True, exist_ok=True)
    return root.resolve()


def normalize_image_mime_type(
    mime_type: str | None,
    *,
    filename: str | None = None,
) -> str | None:
    """Normalize Telegram image mime types and infer from filenames when needed."""
    normalized = (mime_type or "").strip().lower()
    if normalized == "image/jpg":
        normalized = "image/jpeg"
    if normalized in ALLOWED_IMAGE_MIME_TYPES:
        return normalized

    extension = Path(filename or "").suffix.lower()
    if extension in EXTENSION_MIME:
        return EXTENSION_MIME[extension]

    if normalized in {"", "application/octet-stream", "binary/octet-stream"}:
        return EXTENSION_MIME.get(extension)

    return None


def build_media_storage_key(
    *,
    telegram_chat_id: int,
    telegram_message_id: int,
    mime_type: str,
) -> str:
    """Build a deterministic, path-safe storage key for one inquiry image."""
    extension = _extension_for_mime(mime_type)
    return f"chat_{telegram_chat_id}/{telegram_message_id}{extension}"


def validate_storage_key(storage_key: str) -> None:
    """Reject unsafe or malformed inquiry media storage keys."""
    if not storage_key or storage_key.startswith(("/", "\\")):
        raise InvalidInquiryMediaStorageKeyError("Invalid inquiry media storage key")
    if "\\" in storage_key or ".." in storage_key:
        raise InvalidInquiryMediaStorageKeyError("Invalid inquiry media storage key")

    parts = storage_key.split("/")
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise InvalidInquiryMediaStorageKeyError("Invalid inquiry media storage key")

    chat_part, file_part = parts
    if not CHAT_COMPONENT_PATTERN.fullmatch(chat_part):
        raise InvalidInquiryMediaStorageKeyError("Invalid inquiry media storage key")
    if not MESSAGE_FILE_PATTERN.fullmatch(file_part):
        raise InvalidInquiryMediaStorageKeyError("Invalid inquiry media storage key")

    extension = Path(file_part).suffix.lower()
    if extension not in ALLOWED_STORAGE_EXTENSIONS:
        raise InvalidInquiryMediaStorageKeyError("Invalid inquiry media storage key")


def media_path_for_key(settings: Settings, storage_key: str) -> Path:
    """Resolve one validated storage key to an on-disk media path."""
    validate_storage_key(storage_key)
    root = resolve_inquiry_media_root(settings)
    candidate = (root / storage_key).resolve()
    if root not in candidate.parents:
        raise InvalidInquiryMediaStorageKeyError("Invalid inquiry media storage key")
    return candidate


def _extension_for_mime(mime_type: str) -> str:
    extension = MIME_EXTENSION.get(mime_type)
    if extension is None or extension not in ALLOWED_STORAGE_EXTENSIONS:
        raise InvalidInquiryMediaStorageKeyError(
            f"Unsupported inquiry media mime type: {mime_type}"
        )
    return extension

from __future__ import annotations

import mimetypes
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
STORAGE_KEY_PATTERN = re.compile(r"^[0-9]+/[0-9]+\.[a-z0-9]+$")


def resolve_inquiry_media_root(settings: Settings) -> Path:
    """Return the configured inquiry media directory, creating it when needed."""
    root = Path(settings.inquiry_media_dir)
    if not root.is_absolute():
        root = Path.cwd() / root
    root.mkdir(parents=True, exist_ok=True)
    return root


def build_media_storage_key(
    *,
    telegram_chat_id: int,
    telegram_message_id: int,
    mime_type: str,
) -> str:
    """Build a deterministic, path-safe storage key for one inquiry image."""
    extension = MIME_EXTENSION.get(mime_type, mimetypes.guess_extension(mime_type) or ".bin")
    return f"{telegram_chat_id}/{telegram_message_id}{extension}"


def media_path_for_key(settings: Settings, storage_key: str) -> Path:
    """Resolve one validated storage key to an on-disk media path."""
    if not STORAGE_KEY_PATTERN.fullmatch(storage_key):
        raise ValueError("Invalid inquiry media storage key")
    root = resolve_inquiry_media_root(settings)
    candidate = (root / storage_key).resolve()
    if root.resolve() not in candidate.parents and candidate != root.resolve():
        raise ValueError("Invalid inquiry media storage key")
    return candidate

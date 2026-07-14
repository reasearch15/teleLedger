"""One-off production inquiry diagnostics. Run on VPS only."""
from __future__ import annotations

import asyncio
from pathlib import Path

from sqlalchemy import text

from app.core.config import get_settings
from app.db.session import SessionFactory
from app.telegram.inquiry_media import media_path_for_key


async def main() -> None:
    settings = get_settings()
    async with SessionFactory() as session:
        rows = await session.execute(
            text(
                """
                SELECT id, telegram_message_id, telegram_chat_id, message_source,
                       media_type, media_mime_type, media_storage_key,
                       media_download_status, media_size_bytes, media_filename,
                       sender_display_name, caption, text, direction
                FROM inquiry_messages
                ORDER BY id
                """
            )
        )
        print("=== inquiry_messages ===")
        for row in rows.all():
            print(dict(row._mapping))

    async with SessionFactory() as session:
        pending = await session.execute(
            text(
                """
                SELECT id, telegram_message_id, media_download_status, media_storage_key
                FROM inquiry_messages
                WHERE media_download_status IN ('pending', 'failed')
                ORDER BY id
                """
            )
        )
        print("=== pending/failed media ===")
        for row in pending.all():
            print(dict(row._mapping))

    media_root = Path(settings.inquiry_media_dir)
    if not media_root.is_absolute():
        media_root = Path("/opt/teleledger/backend") / media_root
    print(f"=== media root: {media_root.resolve()} ===")
    if media_root.exists():
        for path in sorted(media_root.rglob("*")):
            if path.is_file():
                print(f"FILE {path} size={path.stat().st_size}")

    async with SessionFactory() as session:
        ready = await session.execute(
            text(
                """
                SELECT id, media_storage_key
                FROM inquiry_messages
                WHERE media_storage_key IS NOT NULL
                """
            )
        )
        print("=== storage key resolution ===")
        for row in ready.all():
            key = row.media_storage_key
            try:
                resolved = media_path_for_key(settings, key)
                exists = resolved.exists()
                print(
                    {
                        "id": row.id,
                        "key": key,
                        "path": str(resolved),
                        "exists": exists,
                        "size": resolved.stat().st_size if exists else None,
                    }
                )
            except Exception as error:
                print({"id": row.id, "key": key, "error": str(error)})


if __name__ == "__main__":
    asyncio.run(main())

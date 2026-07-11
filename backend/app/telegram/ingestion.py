from app.db.session import SessionFactory
from app.schemas.telegram import IncomingTelegramMessage
from app.services.telegram_ingestion import (
    TelegramIngestionResult,
    TelegramIngestionService,
)
from app.websocket.events import LiveEventType, event_broker


async def ingest_telegram_message(
    incoming: IncomingTelegramMessage,
) -> TelegramIngestionResult:
    """Run shared Telegram ingestion in its own database transaction."""
    async with SessionFactory() as session:
        result = await TelegramIngestionService(session).ingest(incoming)
    if result.payment_inserted and result.payment_event_id is not None:
        await event_broker.publish(
            LiveEventType.PAYMENT_CREATED,
            payment_id=result.payment_event_id,
            broadcast=True,
        )
    return result


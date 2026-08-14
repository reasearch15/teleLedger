from __future__ import annotations

from dataclasses import dataclass

from app.db.session import SessionFactory
from app.models.venmo_confirmation import VenmoConfirmationStatus
from app.services.venmo_confirmation import VenmoConfirmationService


@dataclass(frozen=True, slots=True)
class VenmoConfirmationReconciliationResult:
    request_id: int
    status: str
    sync_result: str


async def reconcile_venmo_confirmation_telegram_state(
    *,
    request_id: int,
    gateway: object,
) -> VenmoConfirmationReconciliationResult:
    """Repair Telegram presentation from confirmation DB truth only."""
    async with SessionFactory() as session:
        service = VenmoConfirmationService(session)
        request = await service._repository.get_by_id(request_id)  # noqa: SLF001
        if request is None:
            return VenmoConfirmationReconciliationResult(
                request_id=request_id,
                status="not_found",
                sync_result="not_found",
            )
        if request.status not in (
            VenmoConfirmationStatus.CONFIRMED,
            VenmoConfirmationStatus.NOT_RECEIVED,
        ):
            return VenmoConfirmationReconciliationResult(
                request_id=request_id,
                status=request.status.value,
                sync_result="not_terminal",
            )
        attempt = await service._repository.latest_attempt_for_request(request.id)  # noqa: SLF001
        sync_result = await service.sync_telegram_terminal_message(
            request=request,
            attempt=attempt,
            gateway=gateway,
        )
        await session.commit()
        return VenmoConfirmationReconciliationResult(
            request_id=request.id,
            status=request.status.value,
            sync_result=sync_result,
        )

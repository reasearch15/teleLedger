from fastapi import APIRouter

from app.api.routes.admin import router as admin_router
from app.api.routes.auth import router as auth_router
from app.api.routes.cashouts import router as cashouts_router
from app.api.routes.events import router as events_router
from app.api.routes.health import router as health_router
from app.api.routes.ledger import router as ledger_router
from app.api.routes.payment_updates import router as payment_updates_router
from app.api.routes.payments import router as payments_router

api_router = APIRouter()
api_router.include_router(health_router)
api_router.include_router(auth_router)
api_router.include_router(admin_router)
api_router.include_router(ledger_router)
api_router.include_router(cashouts_router)
api_router.include_router(payments_router)
api_router.include_router(events_router)
api_router.include_router(payment_updates_router)

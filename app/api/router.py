from fastapi import APIRouter

from app.api.now_playing import router as now_playing_router
from app.api.v1.event_statistics_access import router as event_statistics_access_router
from app.api.v1.feedback import router as feedback_router
from app.api.v1.mobile_card import router as mobile_card_router
from app.api.v1.mobile_shifts import router as mobile_shifts_router
from app.api.v1.stats import router as stats_router
from app.api.v1.telemetry import router as telemetry_router
from app.api.v1.volunteer_prospects import router as volunteer_prospects_router

from app.api.v1.event_interest import router as event_interest_router

from app.api.v1.booking_requests import router as booking_requests_router

api_router = APIRouter()
api_router.include_router(booking_requests_router, prefix="/api/v1/booking-requests", tags=["booking-requests"])
api_router.include_router(event_interest_router, prefix="/api/v1/event-interest", tags=["event-interest"])
api_router.include_router(now_playing_router, prefix="/api", tags=["now-playing"])
api_router.include_router(feedback_router, prefix="/api/v1/feedback", tags=["feedback"])
api_router.include_router(
    mobile_card_router, prefix="/api/v1/mobile-card", tags=["mobile-card"]
)
api_router.include_router(
    mobile_shifts_router, prefix="/api/v1/mobile-card/shifts", tags=["mobile-shifts"]
)
api_router.include_router(
    volunteer_prospects_router,
    prefix="/api/v1/volunteer-prospects",
    tags=["volunteer-prospects"],
)
api_router.include_router(stats_router, prefix="/api/v1/stats", tags=["stats"])
api_router.include_router(
    event_statistics_access_router,
    prefix="/api/v1/me/statistikk-tilgang",
    tags=["event-statistics"],
)
api_router.include_router(
    telemetry_router, prefix="/api/v1/telemetry/client-errors", tags=["telemetry"]
)

from fastapi import APIRouter, Depends

from app.dependencies import load_web_navigation_state

from app.web.routes.admin_accounts.actions import router as admin_account_actions_router
from app.web.routes.admin_accounts.pages import router as admin_account_pages_router
from app.web.routes.agent.routes import router as agent_router
from app.web.routes.auth.routes import router as auth_router
from app.web.routes.courses.actions import router as course_actions_router
from app.web.routes.courses.pages import router as course_pages_router
from app.web.routes.event_statistics.routes import router as event_statistics_router
from app.web.routes.feedback.routes import router as feedback_router
from app.web.routes.email_deliveries.actions import (
    router as email_delivery_actions_router,
)
from app.web.routes.email_deliveries.pages import router as email_delivery_pages_router
from app.web.routes.groups.actions import router as group_actions_router
from app.web.routes.groups.pages import router as group_pages_router
from app.web.routes.shifts.actions import router as shift_actions_router
from app.web.routes.shifts.pages import router as shift_pages_router
from app.web.routes.spotify.pages import router as spotify_pages_router
from app.web.routes.volunteer_applications.actions import (
    router as volunteer_application_actions_router,
)
from app.web.routes.volunteer_applications.pages import (
    router as volunteer_application_pages_router,
)
from app.web.routes.volunteers.actions import router as volunteer_actions_router
from app.web.routes.volunteers.fragments import router as volunteer_fragments_router
from app.web.routes.volunteers.pages import router as volunteer_pages_router
from app.web.routes.volunteers.search_fragments import (
    router as volunteer_search_fragments_router,
)
from app.web.routes.volunteers.search_pages import (
    router as volunteer_search_pages_router,
)

web_router = APIRouter(dependencies=[Depends(load_web_navigation_state)])
web_router.include_router(auth_router)
web_router.include_router(agent_router)
web_router.include_router(feedback_router)
web_router.include_router(volunteer_search_fragments_router)
web_router.include_router(volunteer_search_pages_router)
web_router.include_router(volunteer_fragments_router)
web_router.include_router(volunteer_actions_router)
web_router.include_router(volunteer_pages_router)
web_router.include_router(group_pages_router)
web_router.include_router(group_actions_router)
web_router.include_router(course_pages_router)
web_router.include_router(course_actions_router)
web_router.include_router(shift_pages_router)
web_router.include_router(shift_actions_router)
web_router.include_router(event_statistics_router)
web_router.include_router(spotify_pages_router)
web_router.include_router(admin_account_pages_router)
web_router.include_router(admin_account_actions_router)
web_router.include_router(email_delivery_pages_router)
web_router.include_router(email_delivery_actions_router)
web_router.include_router(volunteer_application_pages_router)
web_router.include_router(volunteer_application_actions_router)

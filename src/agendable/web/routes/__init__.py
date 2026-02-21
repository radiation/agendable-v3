from __future__ import annotations

from fastapi import APIRouter

from agendable.web.routes.admin import router as admin_router
from agendable.web.routes.auth import router as auth_router
from agendable.web.routes.occurrences import router as occurrences_router
from agendable.web.routes.series import router as series_router

router = APIRouter()
router.include_router(series_router)
router.include_router(auth_router)
router.include_router(occurrences_router)
router.include_router(admin_router)

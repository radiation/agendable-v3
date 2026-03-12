from __future__ import annotations

from typing import Annotated

from fastapi import Depends
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db import get_session
from agendable.providers import (
    build_dashboard_service,
    build_google_calendar_sync_service,
    build_google_imported_series_service,
)
from agendable.services.dashboard_service import DashboardService
from agendable.services.google_calendar_sync_service import GoogleCalendarSyncService
from agendable.services.google_imported_series_service import GoogleImportedSeriesService
from agendable.settings import Settings, get_settings

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_dashboard_service(
    session: SessionDep,
) -> DashboardService:
    return build_dashboard_service(session=session)


def get_google_imported_series_service(
    session: SessionDep,
) -> GoogleImportedSeriesService:
    return build_google_imported_series_service(session=session)


def get_google_calendar_sync_service(
    session: SessionDep,
    settings: SettingsDep,
) -> GoogleCalendarSyncService:
    return build_google_calendar_sync_service(session=session, settings=settings)

from __future__ import annotations

from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.repos import (
    ExternalCalendarConnectionRepository,
    ExternalCalendarEventMirrorRepository,
)
from agendable.services.calendar_event_mapping_service import CalendarEventMappingService
from agendable.services.dashboard_service import DashboardService
from agendable.services.external_calendar_api import ExternalCalendarClient
from agendable.services.google_calendar_client import GoogleCalendarHttpClient
from agendable.services.google_calendar_sync_service import GoogleCalendarSyncService
from agendable.services.google_imported_series_service import GoogleImportedSeriesService
from agendable.settings import Settings


def build_dashboard_service(*, session: AsyncSession) -> DashboardService:
    return DashboardService.from_session(session)


def build_calendar_event_mapping_service(*, session: AsyncSession) -> CalendarEventMappingService:
    return CalendarEventMappingService.from_session(session)


def build_google_imported_series_service(*, session: AsyncSession) -> GoogleImportedSeriesService:
    return GoogleImportedSeriesService.from_session(session)


def build_google_calendar_sync_service(
    *,
    session: AsyncSession,
    settings: Settings,
    calendar_client: ExternalCalendarClient | None = None,
) -> GoogleCalendarSyncService:
    resolved_calendar_client = calendar_client or GoogleCalendarHttpClient(
        api_base_url=settings.google_calendar_api_base_url,
        initial_sync_days_back=settings.google_calendar_initial_sync_days_back,
    )
    return GoogleCalendarSyncService(
        connection_repo=ExternalCalendarConnectionRepository(session),
        event_mirror_repo=ExternalCalendarEventMirrorRepository(session),
        calendar_client=resolved_calendar_client,
        event_mapper=build_calendar_event_mapping_service(session=session),
        settings=settings,
    )

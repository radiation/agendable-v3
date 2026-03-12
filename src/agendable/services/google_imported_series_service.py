from __future__ import annotations

import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import (
    CalendarProvider,
    ImportedSeriesDecision,
    MeetingSeries,
)
from agendable.db.repos import (
    ExternalCalendarConnectionRepository,
    ExternalCalendarEventMirrorRepository,
    MeetingOccurrenceRepository,
    MeetingSeriesRepository,
)
from agendable.services.calendar_event_mapping_service import CalendarEventMappingService


class ImportedSeriesNotFoundError(Exception):
    pass


class MissingGoogleCalendarConnectionError(Exception):
    pass


class GoogleImportedSeriesService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        series_repo: MeetingSeriesRepository | None = None,
        mirror_repo: ExternalCalendarEventMirrorRepository | None = None,
        occurrence_repo: MeetingOccurrenceRepository | None = None,
        connection_repo: ExternalCalendarConnectionRepository | None = None,
        event_mapper: CalendarEventMappingService | None = None,
    ) -> None:
        self.session = session
        self.series_repo = series_repo or MeetingSeriesRepository(session)
        self.mirror_repo = mirror_repo or ExternalCalendarEventMirrorRepository(session)
        self.occurrence_repo = occurrence_repo or MeetingOccurrenceRepository(session)
        self.connection_repo = connection_repo or ExternalCalendarConnectionRepository(session)
        self.event_mapper = event_mapper or CalendarEventMappingService.from_session(session)

    @classmethod
    def from_session(cls, session: AsyncSession) -> GoogleImportedSeriesService:
        return cls(
            session=session,
            series_repo=MeetingSeriesRepository(session),
            mirror_repo=ExternalCalendarEventMirrorRepository(session),
            occurrence_repo=MeetingOccurrenceRepository(session),
            connection_repo=ExternalCalendarConnectionRepository(session),
            event_mapper=CalendarEventMappingService.from_session(session),
        )

    async def keep_pending_google_series(self, *, user_id: uuid.UUID, series_id: uuid.UUID) -> None:
        series = await self._get_pending_google_series_for_user(
            user_id=user_id, series_id=series_id
        )

        connection = await self.connection_repo.get_for_user_provider_calendar(
            user_id=user_id,
            provider=CalendarProvider.google,
            external_calendar_id="primary",
        )
        if connection is None:
            raise MissingGoogleCalendarConnectionError

        series.import_decision = ImportedSeriesDecision.kept

        import_series_id = series.import_external_series_id
        if import_series_id:
            mirrors = await self.mirror_repo.list_for_connection_recurring_event(
                connection_id=connection.id,
                recurring_event_id=import_series_id,
            )
            if mirrors:
                await self.event_mapper.map_mirrors(
                    connection=connection,
                    mirrors=mirrors,
                    recurring_event_details_by_id=None,
                )

        await self.session.commit()

    async def reject_pending_google_series(
        self,
        *,
        user_id: uuid.UUID,
        series_id: uuid.UUID,
    ) -> None:
        series = await self._get_pending_google_series_for_user(
            user_id=user_id, series_id=series_id
        )

        # Keep the series row as a tombstone so this external recurring ID stays rejected.
        series.import_decision = ImportedSeriesDecision.rejected

        mirrors = await self.mirror_repo.list_linked_to_series(series_id=series.id)
        for mirror in mirrors:
            mirror.linked_occurrence_id = None

        occurrences = await self.occurrence_repo.list_for_series(series.id)
        for occurrence in occurrences:
            await self.session.delete(occurrence)

        await self.session.commit()

    async def _get_pending_google_series_for_user(
        self,
        *,
        user_id: uuid.UUID,
        series_id: uuid.UUID,
    ) -> MeetingSeries:
        series = await self.series_repo.get_pending_google_import_for_owner(
            series_id=series_id,
            owner_user_id=user_id,
        )
        if series is None:
            raise ImportedSeriesNotFoundError

        return series

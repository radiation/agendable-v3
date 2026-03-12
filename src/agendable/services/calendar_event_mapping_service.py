from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import (
    CalendarProvider,
    ExternalCalendarConnection,
    ExternalCalendarEventMirror,
    ImportedSeriesDecision,
    MeetingOccurrence,
    MeetingSeries,
)
from agendable.db.repos import (
    MeetingOccurrenceRepository,
    MeetingSeriesRepository,
)
from agendable.services.external_calendar_api import ExternalRecurringEventDetails
from agendable.services.occurrence_service import complete_occurrence_and_roll_forward


class CalendarEventMappingService:
    def __init__(
        self,
        *,
        session: AsyncSession,
        occurrence_repo: MeetingOccurrenceRepository | None = None,
        series_repo: MeetingSeriesRepository | None = None,
    ) -> None:
        self.session = session
        self.occurrence_repo = occurrence_repo or MeetingOccurrenceRepository(session)
        self.series_repo = series_repo or MeetingSeriesRepository(session)

    @classmethod
    def from_session(cls, session: AsyncSession) -> CalendarEventMappingService:
        return cls(
            session=session,
            occurrence_repo=MeetingOccurrenceRepository(session),
            series_repo=MeetingSeriesRepository(session),
        )

    async def map_mirrors(
        self,
        *,
        connection: ExternalCalendarConnection,
        mirrors: list[ExternalCalendarEventMirror],
        recurring_event_details_by_id: dict[str, ExternalRecurringEventDetails] | None = None,
    ) -> int:
        details_by_id = recurring_event_details_by_id or {}
        mapped_count = 0
        for mirror in mirrors:
            mapped_count += await self._map_single_mirror(
                connection=connection,
                mirror=mirror,
                recurring_event_details_by_id=details_by_id,
            )
        await self.session.flush()
        return mapped_count

    async def _map_single_mirror(
        self,
        *,
        connection: ExternalCalendarConnection,
        mirror: ExternalCalendarEventMirror,
        recurring_event_details_by_id: dict[str, ExternalRecurringEventDetails],
    ) -> int:
        linked_occurrence = await self._get_linked_occurrence(mirror)

        if mirror.external_status == "cancelled":
            if linked_occurrence is None:
                return 0
            await complete_occurrence_and_roll_forward(
                self.session,
                occurrence=linked_occurrence,
                commit=False,
                create_next_if_missing=True,
            )
            return 1

        if mirror.start_at is None or mirror.is_all_day:
            return 0

        # Prevent importing one-off calendar meetings as weekly Agendable series.
        # We only map recurring events (Google instances will have `recurringEventId`).
        if mirror.external_recurring_event_id is None and linked_occurrence is None:
            return 0

        normalized_start = mirror.start_at.astimezone(UTC)
        series = await self._resolve_series(
            connection=connection,
            mirror=mirror,
            linked_occurrence=linked_occurrence,
            recurring_event_details_by_id=recurring_event_details_by_id,
        )

        if series.import_decision in {
            ImportedSeriesDecision.pending,
            ImportedSeriesDecision.rejected,
        }:
            return 0

        occurrence = linked_occurrence
        if occurrence is None:
            occurrence = await self._find_occurrence_by_schedule(
                series_id=series.id,
                scheduled_at=normalized_start,
            )

        if occurrence is None:
            occurrence = MeetingOccurrence(
                series_id=series.id,
                scheduled_at=normalized_start,
                notes="",
                is_completed=False,
            )
            await self.occurrence_repo.add(occurrence)
        else:
            occurrence.scheduled_at = normalized_start
            occurrence.is_completed = False

        mirror.linked_occurrence_id = occurrence.id
        return 1

    async def _get_linked_occurrence(
        self,
        mirror: ExternalCalendarEventMirror,
    ) -> MeetingOccurrence | None:
        if mirror.linked_occurrence_id is None:
            return None
        return await self.occurrence_repo.get_by_id(mirror.linked_occurrence_id)

    async def _resolve_series(
        self,
        *,
        connection: ExternalCalendarConnection,
        mirror: ExternalCalendarEventMirror,
        linked_occurrence: MeetingOccurrence | None,
        recurring_event_details_by_id: dict[str, ExternalRecurringEventDetails],
    ) -> MeetingSeries:
        if linked_occurrence is not None:
            existing_series = await self.series_repo.get(linked_occurrence.series_id)
            if existing_series is not None:
                self._apply_series_title(existing_series, mirror)
                self._apply_series_recurrence(
                    existing_series, mirror, recurring_event_details_by_id
                )
                return existing_series

        group_key = self._series_group_key(mirror)
        existing_series = await self._find_series_for_import_key(
            owner_user_id=connection.user_id,
            import_external_series_id=group_key,
        )
        if existing_series is not None:
            self._apply_series_title(existing_series, mirror)
            self._apply_series_recurrence(existing_series, mirror, recurring_event_details_by_id)
            return existing_series

        existing_series = await self._find_series_for_group_key(
            connection_id=connection.id,
            group_key=group_key,
            is_recurring=mirror.external_recurring_event_id is not None,
        )
        if existing_series is not None:
            existing_series.imported_from_provider = CalendarProvider.google
            existing_series.import_external_series_id = group_key
            if existing_series.import_decision is None:
                existing_series.import_decision = ImportedSeriesDecision.kept
            self._apply_series_title(existing_series, mirror)
            self._apply_series_recurrence(existing_series, mirror, recurring_event_details_by_id)
            return existing_series

        series = MeetingSeries(
            owner_user_id=connection.user_id,
            title=self._series_title(mirror),
            default_interval_days=7,
            reminder_minutes_before=60,
            imported_from_provider=CalendarProvider.google,
            import_external_series_id=group_key,
            import_decision=ImportedSeriesDecision.pending,
        )
        self._apply_series_recurrence(series, mirror, recurring_event_details_by_id)
        await self.series_repo.add(series)
        return series

    async def _find_series_for_import_key(
        self,
        *,
        owner_user_id: uuid.UUID,
        import_external_series_id: str,
    ) -> MeetingSeries | None:
        return await self.series_repo.find_for_google_import_key(
            owner_user_id=owner_user_id,
            import_external_series_id=import_external_series_id,
        )

    def _apply_series_recurrence(
        self,
        series: MeetingSeries,
        mirror: ExternalCalendarEventMirror,
        recurring_event_details_by_id: dict[str, ExternalRecurringEventDetails],
    ) -> None:
        recurring_event_id = mirror.external_recurring_event_id
        if recurring_event_id is None:
            return

        details = recurring_event_details_by_id.get(recurring_event_id)
        if details is None:
            return

        # Only set when we have a usable RRULE.
        if details.recurrence_rrule:
            series.recurrence_rrule = details.recurrence_rrule
            series.recurrence_dtstart = details.recurrence_dtstart
            series.recurrence_timezone = details.recurrence_timezone

    async def _find_series_for_group_key(
        self,
        *,
        connection_id: uuid.UUID,
        group_key: str,
        is_recurring: bool,
    ) -> MeetingSeries | None:
        return await self.series_repo.find_for_connection_group_key(
            connection_id=connection_id,
            group_key=group_key,
            is_recurring=is_recurring,
        )

    async def _find_occurrence_by_schedule(
        self,
        *,
        series_id: uuid.UUID,
        scheduled_at: datetime,
    ) -> MeetingOccurrence | None:
        return await self.occurrence_repo.get_for_series_scheduled_at(
            series_id=series_id,
            scheduled_at=scheduled_at,
        )

    def _apply_series_title(
        self, series: MeetingSeries, mirror: ExternalCalendarEventMirror
    ) -> None:
        summary = (mirror.summary or "").strip()
        if summary:
            series.title = summary

    def _series_title(self, mirror: ExternalCalendarEventMirror) -> str:
        summary = (mirror.summary or "").strip()
        if summary:
            return summary
        return "Imported calendar meeting"

    def _series_group_key(self, mirror: ExternalCalendarEventMirror) -> str:
        recurring = mirror.external_recurring_event_id
        if recurring:
            return recurring
        return mirror.external_event_id

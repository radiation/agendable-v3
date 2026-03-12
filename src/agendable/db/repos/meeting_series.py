from __future__ import annotations

import uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import (
    CalendarProvider,
    ExternalCalendarEventMirror,
    ImportedSeriesDecision,
    MeetingOccurrence,
    MeetingSeries,
)
from agendable.db.repos.base import BaseRepository


class MeetingSeriesRepository(BaseRepository[MeetingSeries]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, MeetingSeries)

    async def list_for_owner(self, owner_user_id: uuid.UUID) -> list[MeetingSeries]:
        result = await self.session.execute(
            select(MeetingSeries)
            .where(
                MeetingSeries.owner_user_id == owner_user_id,
                or_(
                    MeetingSeries.import_decision.is_(None),
                    MeetingSeries.import_decision == ImportedSeriesDecision.kept,
                ),
            )
            .order_by(MeetingSeries.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_for_owner(
        self, series_id: uuid.UUID, owner_user_id: uuid.UUID
    ) -> MeetingSeries | None:
        result = await self.session.execute(
            select(MeetingSeries).where(
                MeetingSeries.id == series_id,
                MeetingSeries.owner_user_id == owner_user_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_pending_google_import_for_owner(
        self,
        *,
        series_id: uuid.UUID,
        owner_user_id: uuid.UUID,
    ) -> MeetingSeries | None:
        result = await self.session.execute(
            select(MeetingSeries).where(
                MeetingSeries.id == series_id,
                MeetingSeries.owner_user_id == owner_user_id,
                MeetingSeries.imported_from_provider == CalendarProvider.google,
                MeetingSeries.import_decision == ImportedSeriesDecision.pending,
            )
        )
        return result.scalar_one_or_none()

    async def find_for_google_import_key(
        self,
        *,
        owner_user_id: uuid.UUID,
        import_external_series_id: str,
    ) -> MeetingSeries | None:
        result = await self.session.execute(
            select(MeetingSeries)
            .where(
                MeetingSeries.owner_user_id == owner_user_id,
                MeetingSeries.imported_from_provider == CalendarProvider.google,
                MeetingSeries.import_external_series_id == import_external_series_id,
            )
            .order_by(MeetingSeries.created_at.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def find_for_connection_group_key(
        self,
        *,
        connection_id: uuid.UUID,
        group_key: str,
        is_recurring: bool,
    ) -> MeetingSeries | None:
        recurring_predicate = (
            ExternalCalendarEventMirror.external_recurring_event_id == group_key
            if is_recurring
            else ExternalCalendarEventMirror.external_event_id == group_key
        )
        result = await self.session.execute(
            select(MeetingSeries)
            .join(MeetingOccurrence, MeetingOccurrence.series_id == MeetingSeries.id)
            .join(
                ExternalCalendarEventMirror,
                ExternalCalendarEventMirror.linked_occurrence_id == MeetingOccurrence.id,
            )
            .where(
                ExternalCalendarEventMirror.connection_id == connection_id,
                recurring_predicate,
            )
            .order_by(MeetingSeries.created_at.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def find_for_connection_recurring_event(
        self,
        *,
        connection_id: uuid.UUID,
        recurring_event_id: str,
    ) -> MeetingSeries | None:
        result = await self.session.execute(
            select(MeetingSeries)
            .join(MeetingOccurrence, MeetingOccurrence.series_id == MeetingSeries.id)
            .join(
                ExternalCalendarEventMirror,
                ExternalCalendarEventMirror.linked_occurrence_id == MeetingOccurrence.id,
            )
            .where(
                ExternalCalendarEventMirror.connection_id == connection_id,
                ExternalCalendarEventMirror.external_recurring_event_id == recurring_event_id,
            )
            .order_by(MeetingSeries.created_at.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

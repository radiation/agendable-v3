from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agendable.db.models import (
    ImportedSeriesDecision,
    MeetingOccurrence,
    MeetingOccurrenceAttendee,
    MeetingSeries,
    Task,
)


class DashboardRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_upcoming_meetings(
        self,
        *,
        user_id: uuid.UUID,
        now: datetime,
        limit: int = 20,
    ) -> list[MeetingOccurrence]:
        result = await self.session.execute(
            select(MeetingOccurrence)
            .options(
                selectinload(MeetingOccurrence.series),
                selectinload(MeetingOccurrence.external_event_mirrors),
            )
            .join(MeetingSeries, MeetingOccurrence.series_id == MeetingSeries.id)
            .outerjoin(
                MeetingOccurrenceAttendee,
                MeetingOccurrenceAttendee.occurrence_id == MeetingOccurrence.id,
            )
            .where(
                or_(
                    MeetingSeries.owner_user_id == user_id,
                    MeetingOccurrenceAttendee.user_id == user_id,
                ),
                or_(
                    MeetingSeries.import_decision.is_(None),
                    MeetingSeries.import_decision == ImportedSeriesDecision.kept,
                ),
                MeetingOccurrence.scheduled_at >= now,
            )
            .distinct()
            .order_by(MeetingOccurrence.scheduled_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def list_outstanding_tasks(
        self,
        *,
        user_id: uuid.UUID,
        limit: int = 200,
    ) -> list[Task]:
        result = await self.session.execute(
            select(Task)
            .join(MeetingOccurrence, Task.occurrence_id == MeetingOccurrence.id)
            .join(MeetingSeries, MeetingOccurrence.series_id == MeetingSeries.id)
            .outerjoin(
                MeetingOccurrenceAttendee,
                MeetingOccurrenceAttendee.occurrence_id == MeetingOccurrence.id,
            )
            .options(
                selectinload(Task.assignee),
                selectinload(Task.occurrence).selectinload(MeetingOccurrence.series),
            )
            .where(
                or_(
                    MeetingSeries.owner_user_id == user_id,
                    MeetingOccurrenceAttendee.user_id == user_id,
                ),
                or_(
                    MeetingSeries.import_decision.is_(None),
                    MeetingSeries.import_decision == ImportedSeriesDecision.kept,
                ),
                Task.is_done.is_(False),
            )
            .distinct()
            .order_by(Task.due_at.asc(), Task.created_at.asc())
            .limit(limit)
        )
        return list(result.scalars().all())

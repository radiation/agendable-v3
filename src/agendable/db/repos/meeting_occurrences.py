from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import MeetingOccurrence
from agendable.db.repos.base import BaseRepository


class MeetingOccurrenceRepository(BaseRepository[MeetingOccurrence]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, MeetingOccurrence)

    async def list_for_series(self, series_id: uuid.UUID) -> list[MeetingOccurrence]:
        result = await self.session.execute(
            select(MeetingOccurrence)
            .where(MeetingOccurrence.series_id == series_id)
            .order_by(MeetingOccurrence.scheduled_at.asc())
        )
        return list(result.scalars().all())

    async def get_by_id(self, occurrence_id: uuid.UUID) -> MeetingOccurrence | None:
        return await self.get(occurrence_id)

    async def get_next_for_series(
        self, series_id: uuid.UUID, scheduled_after: datetime
    ) -> MeetingOccurrence | None:
        result = await self.session.execute(
            select(MeetingOccurrence)
            .where(
                MeetingOccurrence.series_id == series_id,
                MeetingOccurrence.scheduled_at > scheduled_after,
            )
            .order_by(MeetingOccurrence.scheduled_at.asc())
            .limit(1)
        )
        return result.scalar_one_or_none()

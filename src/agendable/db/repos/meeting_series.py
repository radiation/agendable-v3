from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import MeetingSeries
from agendable.db.repos.base import BaseRepository


class MeetingSeriesRepository(BaseRepository[MeetingSeries]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, MeetingSeries)

    async def list_for_owner(self, owner_user_id: uuid.UUID) -> list[MeetingSeries]:
        result = await self.session.execute(
            select(MeetingSeries)
            .where(MeetingSeries.owner_user_id == owner_user_id)
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

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import MeetingOccurrence, Task
from agendable.db.repos import DashboardRepository


class DashboardService:
    def __init__(self, *, dashboard_repo: DashboardRepository) -> None:
        self.dashboard_repo = dashboard_repo

    @classmethod
    def from_session(cls, session: AsyncSession) -> DashboardService:
        return cls(dashboard_repo=DashboardRepository(session))

    async def list_upcoming_meetings(
        self,
        *,
        user_id: uuid.UUID,
        now: datetime,
        limit: int = 20,
    ) -> list[MeetingOccurrence]:
        return await self.dashboard_repo.list_upcoming_meetings(
            user_id=user_id,
            now=now,
            limit=limit,
        )

    async def list_outstanding_tasks(
        self,
        *,
        user_id: uuid.UUID,
        limit: int = 200,
    ) -> list[Task]:
        return await self.dashboard_repo.list_outstanding_tasks(
            user_id=user_id,
            limit=limit,
        )

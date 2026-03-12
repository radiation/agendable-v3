from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agendable.db.models import Task
from agendable.db.repos.base import BaseRepository


class TaskRepository(BaseRepository[Task]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Task)

    async def list_for_occurrence(self, occurrence_id: uuid.UUID) -> list[Task]:
        result = await self.session.execute(
            select(Task)
            .options(selectinload(Task.assignee))
            .where(Task.occurrence_id == occurrence_id)
            .order_by(Task.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_by_id(self, task_id: uuid.UUID) -> Task | None:
        return await self.get(task_id)

    async def reassign_open_tasks(
        self,
        *,
        from_occurrence_id: uuid.UUID,
        to_occurrence_id: uuid.UUID,
        to_due_at: datetime,
    ) -> None:
        await self.session.execute(
            update(Task)
            .where(Task.occurrence_id == from_occurrence_id, Task.is_done.is_(False))
            .values(occurrence_id=to_occurrence_id, due_at=to_due_at)
        )

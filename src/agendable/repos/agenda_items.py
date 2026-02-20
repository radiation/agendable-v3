from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.models import AgendaItem
from agendable.repos.base import BaseRepository


class AgendaItemRepository(BaseRepository[AgendaItem]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, AgendaItem)

    async def list_for_occurrence(self, occurrence_id: uuid.UUID) -> list[AgendaItem]:
        result = await self.session.execute(
            select(AgendaItem)
            .where(AgendaItem.occurrence_id == occurrence_id)
            .order_by(AgendaItem.created_at.desc())
        )
        return list(result.scalars().all())

    async def get_by_id(self, item_id: uuid.UUID) -> AgendaItem | None:
        return await self.get(item_id)

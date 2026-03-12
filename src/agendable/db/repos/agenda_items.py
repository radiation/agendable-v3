from __future__ import annotations

import uuid

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import AgendaItem
from agendable.db.repos.base import BaseRepository


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

    async def reassign_open_items(
        self,
        *,
        from_occurrence_id: uuid.UUID,
        to_occurrence_id: uuid.UUID,
    ) -> None:
        await self.session.execute(
            update(AgendaItem)
            .where(AgendaItem.occurrence_id == from_occurrence_id, AgendaItem.is_done.is_(False))
            .values(occurrence_id=to_occurrence_id)
        )

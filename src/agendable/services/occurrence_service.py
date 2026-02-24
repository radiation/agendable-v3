from __future__ import annotations

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import AgendaItem, MeetingOccurrence, Task
from agendable.db.repos import MeetingOccurrenceRepository


async def complete_occurrence_and_roll_forward(
    session: AsyncSession,
    *,
    occurrence: MeetingOccurrence,
) -> MeetingOccurrence | None:
    occ_repo = MeetingOccurrenceRepository(session)
    next_occurrence = await occ_repo.get_next_for_series(
        occurrence.series_id,
        occurrence.scheduled_at,
    )

    if next_occurrence is not None:
        await session.execute(
            update(Task)
            .where(Task.occurrence_id == occurrence.id, Task.is_done.is_(False))
            .values(occurrence_id=next_occurrence.id, due_at=next_occurrence.scheduled_at)
        )
        await session.execute(
            update(AgendaItem)
            .where(AgendaItem.occurrence_id == occurrence.id, AgendaItem.is_done.is_(False))
            .values(occurrence_id=next_occurrence.id)
        )

    occurrence.is_completed = True
    await session.commit()
    return next_occurrence

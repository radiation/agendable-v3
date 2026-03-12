from __future__ import annotations

import uuid
from datetime import datetime, timedelta
from typing import cast

from sqlalchemy import or_, select, update
from sqlalchemy.engine import CursorResult
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agendable.db.models import (
    MeetingOccurrence,
    MeetingSeries,
    Reminder,
    ReminderDeliveryStatus,
)
from agendable.db.repos.base import BaseRepository


class ReminderRepository(BaseRepository[Reminder]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, Reminder)

    async def list_pending_for_delivery(self) -> list[Reminder]:
        result = await self.session.execute(
            select(Reminder)
            .options(
                selectinload(Reminder.occurrence)
                .selectinload(MeetingOccurrence.series)
                .selectinload(MeetingSeries.owner),
                selectinload(Reminder.occurrence).selectinload(MeetingOccurrence.tasks),
            )
            .where(Reminder.sent_at.is_(None))
            .where(
                Reminder.delivery_status.in_(
                    [ReminderDeliveryStatus.pending, ReminderDeliveryStatus.retry_scheduled]
                )
            )
            .order_by(Reminder.next_attempt_at.asc())
        )
        return list(result.scalars().all())

    async def try_claim_attempt(
        self,
        *,
        reminder_id: uuid.UUID,
        expected_attempt_count: int,
        now: datetime,
        claim_lease_seconds: int,
    ) -> bool:
        claim_result = await self.session.execute(
            update(Reminder)
            .where(Reminder.id == reminder_id)
            .where(Reminder.sent_at.is_(None))
            .where(
                Reminder.delivery_status.in_(
                    [ReminderDeliveryStatus.pending, ReminderDeliveryStatus.retry_scheduled]
                )
            )
            .where(Reminder.attempt_count == expected_attempt_count)
            .where(or_(Reminder.next_attempt_at.is_(None), Reminder.next_attempt_at <= now))
            .values(
                attempt_count=Reminder.attempt_count + 1,
                last_attempted_at=now,
                next_attempt_at=now + timedelta(seconds=claim_lease_seconds),
            )
            .execution_options(synchronize_session=False)
        )

        claim_rowcount = cast(CursorResult[object], claim_result).rowcount
        return claim_rowcount == 1

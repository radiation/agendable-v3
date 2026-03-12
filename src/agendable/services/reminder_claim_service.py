from __future__ import annotations

from datetime import datetime

import agendable.db as db
from agendable.db.models import Reminder
from agendable.db.repos import ReminderRepository


async def claim_reminder_attempt(
    *,
    reminder: Reminder,
    now: datetime,
    claim_lease_seconds: int,
) -> bool:
    async with db.SessionMaker() as claim_session:
        reminder_repo = ReminderRepository(claim_session)
        was_claimed = await reminder_repo.try_claim_attempt(
            reminder_id=reminder.id,
            expected_attempt_count=reminder.attempt_count,
            now=now,
            claim_lease_seconds=claim_lease_seconds,
        )
        if not was_claimed:
            return False
        await claim_session.commit()

    reminder.attempt_count += 1
    reminder.last_attempted_at = now
    return True

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime

from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import MeetingOccurrence, MeetingSeries
from agendable.recurrence import generate_datetimes
from agendable.reminders import build_default_email_reminder
from agendable.settings import Settings


async def create_series_with_occurrences(
    session: AsyncSession,
    *,
    owner_user_id: uuid.UUID,
    title: str,
    reminder_minutes_before: int,
    recurrence_rrule: str,
    recurrence_dtstart: datetime,
    recurrence_timezone: str,
    generate_count: int,
    settings: Settings,
) -> tuple[MeetingSeries, list[MeetingOccurrence]]:
    series = MeetingSeries(
        owner_user_id=owner_user_id,
        title=title,
        default_interval_days=7,
        reminder_minutes_before=reminder_minutes_before,
        recurrence_rrule=recurrence_rrule,
        recurrence_dtstart=recurrence_dtstart,
        recurrence_timezone=recurrence_timezone.strip(),
    )
    session.add(series)
    await session.flush()

    scheduled: Sequence[datetime] = generate_datetimes(
        rrule=recurrence_rrule,
        dtstart=recurrence_dtstart,
        count=generate_count,
    )
    if not scheduled:
        raise ValueError("RRULE produced no occurrences")

    occurrences = [
        MeetingOccurrence(
            series_id=series.id,
            scheduled_at=dt.astimezone(UTC),
            notes="",
        )
        for dt in scheduled
    ]
    session.add_all(occurrences)
    await session.flush()

    if settings.enable_default_email_reminders:
        session.add_all(
            [
                build_default_email_reminder(
                    occurrence_id=occ.id,
                    occurrence_scheduled_at=occ.scheduled_at,
                    settings=settings,
                    lead_minutes_before=series.reminder_minutes_before,
                )
                for occ in occurrences
            ]
        )

    await session.commit()
    return series, occurrences

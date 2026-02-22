from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import agendable.db as db
from agendable.cli import _run_due_reminders
from agendable.db.models import (
    MeetingOccurrence,
    MeetingSeries,
    Reminder,
    ReminderChannel,
    Task,
    User,
)
from agendable.reminders import ReminderEmail, ReminderSender


@dataclass
class CapturingSender(ReminderSender):
    sent: list[ReminderEmail]

    async def send_email_reminder(self, reminder: ReminderEmail) -> None:
        self.sent.append(reminder)


async def _create_occurrence(
    db_session: AsyncSession, *, email: str, title: str
) -> MeetingOccurrence:
    owner = User(
        email=email,
        first_name="Test",
        last_name="Owner",
        display_name="Test Owner",
        timezone="UTC",
        password_hash=None,
    )
    db_session.add(owner)
    await db_session.flush()

    series = MeetingSeries(owner_user_id=owner.id, title=title, default_interval_days=7)
    db_session.add(series)
    await db_session.flush()

    occurrence = MeetingOccurrence(
        series_id=series.id,
        scheduled_at=datetime.now(UTC) + timedelta(days=1),
        notes="",
        is_completed=False,
    )
    db_session.add(occurrence)
    await db_session.commit()
    await db_session.refresh(occurrence)
    return occurrence


@pytest.mark.asyncio
async def test_run_due_reminders_sends_due_email_and_marks_sent(db_session: AsyncSession) -> None:
    occurrence = await _create_occurrence(
        db_session,
        email="owner-reminder@example.com",
        title="Weekly 1:1",
    )

    due_reminder = Reminder(
        occurrence_id=occurrence.id,
        channel=ReminderChannel.email,
        send_at=datetime.now(UTC) - timedelta(minutes=1),
        sent_at=None,
    )
    future_reminder = Reminder(
        occurrence_id=occurrence.id,
        channel=ReminderChannel.email,
        send_at=datetime.now(UTC) + timedelta(hours=1),
        sent_at=None,
    )
    db_session.add_all([due_reminder, future_reminder])
    series = (
        await db_session.execute(
            select(MeetingSeries).where(MeetingSeries.id == occurrence.series_id)
        )
    ).scalar_one()
    db_session.add_all(
        [
            Task(
                occurrence_id=occurrence.id,
                assigned_user_id=series.owner_user_id,
                due_at=occurrence.scheduled_at,
                title="Prepare agenda",
                is_done=False,
            ),
            Task(
                occurrence_id=occurrence.id,
                assigned_user_id=series.owner_user_id,
                due_at=occurrence.scheduled_at,
                title="Already complete",
                is_done=True,
            ),
        ]
    )
    await db_session.commit()

    sender = CapturingSender(sent=[])
    await _run_due_reminders(sender=sender)

    assert len(sender.sent) == 1
    sent_payload = sender.sent[0]
    assert sent_payload.recipient_email == "owner-reminder@example.com"
    assert sent_payload.meeting_title == "Weekly 1:1"
    assert sent_payload.incomplete_tasks == ["Prepare agenda"]

    async with db.SessionMaker() as verify_session:
        refreshed_due = (
            await verify_session.execute(select(Reminder).where(Reminder.id == due_reminder.id))
        ).scalar_one()
        refreshed_future = (
            await verify_session.execute(select(Reminder).where(Reminder.id == future_reminder.id))
        ).scalar_one()

        assert refreshed_due.sent_at is not None
        assert refreshed_future.sent_at is None


@pytest.mark.asyncio
async def test_run_due_reminders_skips_non_email_channels(db_session: AsyncSession) -> None:
    occurrence = await _create_occurrence(
        db_session,
        email="owner-slack@example.com",
        title="Standup",
    )

    slack_reminder = Reminder(
        occurrence_id=occurrence.id,
        channel=ReminderChannel.slack,
        send_at=datetime.now(UTC) - timedelta(minutes=1),
        sent_at=None,
    )
    db_session.add(slack_reminder)
    await db_session.commit()

    sender = CapturingSender(sent=[])
    await _run_due_reminders(sender=sender)

    assert sender.sent == []

    async with db.SessionMaker() as verify_session:
        refreshed = (
            await verify_session.execute(select(Reminder).where(Reminder.id == slack_reminder.id))
        ).scalar_one()
        assert refreshed.sent_at is None

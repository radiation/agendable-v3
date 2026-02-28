from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import (
    MeetingOccurrence,
    MeetingSeries,
    Reminder,
    ReminderChannel,
    User,
)
from agendable.testing.web_test_helpers import login_user


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@pytest.mark.asyncio
async def test_create_occurrence_404_when_series_not_owned(client: AsyncClient) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    resp = await client.post(
        f"/series/{uuid.uuid4()}/occurrences",
        data={"scheduled_at": "2030-01-10T09:00:00Z"},
        follow_redirects=False,
    )

    assert resp.status_code == 404


async def test_create_series_auto_creates_email_reminders(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    title = f"Reminders {uuid.uuid4()}"
    resp = await client.post(
        "/series",
        data={
            "title": title,
            "reminder_minutes_before": 120,
            "recurrence_start_date": "2030-01-01",
            "recurrence_time": "09:00",
            "recurrence_timezone": "UTC",
            "recurrence_freq": "DAILY",
            "recurrence_interval": 1,
            "generate_count": 2,
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    alice = (
        await db_session.execute(select(User).where(User.email == "alice@example.com"))
    ).scalar_one()
    series = (
        await db_session.execute(
            select(MeetingSeries).where(
                MeetingSeries.owner_user_id == alice.id,
                MeetingSeries.title == title,
            )
        )
    ).scalar_one()

    occs = (
        (
            await db_session.execute(
                select(MeetingOccurrence)
                .where(MeetingOccurrence.series_id == series.id)
                .order_by(MeetingOccurrence.scheduled_at.asc())
            )
        )
        .scalars()
        .all()
    )
    reminders = (
        (
            await db_session.execute(
                select(Reminder)
                .join(MeetingOccurrence, Reminder.occurrence_id == MeetingOccurrence.id)
                .where(MeetingOccurrence.series_id == series.id)
                .order_by(MeetingOccurrence.scheduled_at.asc())
            )
        )
        .scalars()
        .all()
    )

    assert len(reminders) == len(occs) == 2
    for occ, reminder in zip(occs, reminders, strict=True):
        assert reminder.channel == ReminderChannel.email
        assert _as_utc(reminder.send_at) == _as_utc(occ.scheduled_at) - timedelta(minutes=120)


async def test_create_series_rejects_invalid_reminder_minutes_before(client: AsyncClient) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    resp = await client.post(
        "/series",
        data={
            "title": "Invalid Reminder",
            "reminder_minutes_before": -1,
            "recurrence_start_date": "2030-01-01",
            "recurrence_time": "09:00",
            "recurrence_timezone": "UTC",
            "recurrence_freq": "DAILY",
            "recurrence_interval": 1,
            "generate_count": 1,
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "reminder_minutes_before must be between 0 and 43200"


async def test_manual_occurrence_creation_auto_creates_email_reminder(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    title = f"Manual reminder {uuid.uuid4()}"
    create_resp = await client.post(
        "/series",
        data={
            "title": title,
            "reminder_minutes_before": 45,
            "recurrence_start_date": "2030-01-01",
            "recurrence_time": "09:00",
            "recurrence_timezone": "UTC",
            "recurrence_freq": "DAILY",
            "recurrence_interval": 1,
            "generate_count": 1,
        },
        follow_redirects=True,
    )
    assert create_resp.status_code == 200

    alice = (
        await db_session.execute(select(User).where(User.email == "alice@example.com"))
    ).scalar_one()
    series = (
        await db_session.execute(
            select(MeetingSeries).where(
                MeetingSeries.owner_user_id == alice.id,
                MeetingSeries.title == title,
            )
        )
    ).scalar_one()

    resp = await client.post(
        f"/series/{series.id}/occurrences",
        data={"scheduled_at": "2030-01-10T09:00:00Z"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    manual_occ = (
        (
            await db_session.execute(
                select(MeetingOccurrence)
                .where(MeetingOccurrence.series_id == series.id)
                .order_by(MeetingOccurrence.scheduled_at.desc())
            )
        )
        .scalars()
        .first()
    )
    assert manual_occ is not None

    reminder = (
        (
            await db_session.execute(
                select(Reminder)
                .where(Reminder.occurrence_id == manual_occ.id)
                .order_by(Reminder.send_at.desc())
            )
        )
        .scalars()
        .first()
    )
    assert reminder is not None
    assert reminder.channel == ReminderChannel.email
    assert _as_utc(reminder.send_at) == _as_utc(manual_occ.scheduled_at) - timedelta(minutes=45)


async def test_manual_occurrence_creation_skips_reminder_when_default_disabled(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENDABLE_ENABLE_DEFAULT_EMAIL_REMINDERS", "false")
    await login_user(client, "alice@example.com", "pw-alice")

    title = f"Manual no reminder {uuid.uuid4()}"
    create_resp = await client.post(
        "/series",
        data={
            "title": title,
            "reminder_minutes_before": 45,
            "recurrence_start_date": "2030-01-01",
            "recurrence_time": "09:00",
            "recurrence_timezone": "UTC",
            "recurrence_freq": "DAILY",
            "recurrence_interval": 1,
            "generate_count": 1,
        },
        follow_redirects=True,
    )
    assert create_resp.status_code == 200

    alice = (
        await db_session.execute(select(User).where(User.email == "alice@example.com"))
    ).scalar_one()
    series = (
        await db_session.execute(
            select(MeetingSeries).where(
                MeetingSeries.owner_user_id == alice.id,
                MeetingSeries.title == title,
            )
        )
    ).scalar_one()

    resp = await client.post(
        f"/series/{series.id}/occurrences",
        data={"scheduled_at": "2030-01-10T09:00:00Z"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    manual_occ = (
        (
            await db_session.execute(
                select(MeetingOccurrence)
                .where(MeetingOccurrence.series_id == series.id)
                .order_by(MeetingOccurrence.scheduled_at.desc())
            )
        )
        .scalars()
        .first()
    )
    assert manual_occ is not None

    reminder = (
        (
            await db_session.execute(
                select(Reminder)
                .where(Reminder.occurrence_id == manual_occ.id)
                .order_by(Reminder.send_at.desc())
            )
        )
        .scalars()
        .first()
    )
    assert reminder is None

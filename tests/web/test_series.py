from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import MeetingOccurrence, MeetingSeries, Reminder, ReminderChannel, User


async def _login(client: AsyncClient, email: str, password: str) -> None:
    # Tests run with a fresh DB; create the account explicitly.
    resp = await client.post(
        "/signup",
        data={
            "first_name": "Test",
            "last_name": "User",
            "timezone": "UTC",
            "email": email,
            "password": password,
        },
        follow_redirects=True,
    )
    if resp.status_code == 200:
        return

    # If the account already exists, sign in.
    resp = await client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=True,
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_create_series_requires_auth(client: AsyncClient) -> None:
    resp = await client.post(
        "/series",
        data={
            "title": "X",
            "recurrence_start_date": "2030-01-01",
            "recurrence_time": "09:00",
            "recurrence_timezone": "UTC",
            "recurrence_freq": "DAILY",
            "recurrence_interval": 1,
            "generate_count": 1,
        },
    )
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_series_and_list_is_scoped_to_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    title = f"1:1 {uuid.uuid4()}"

    await _login(client, "alice@example.com", "pw-alice")

    resp = await client.post(
        "/series",
        data={
            "title": title,
            "reminder_minutes_before": 60,
            "recurrence_start_date": "2030-01-01",
            "recurrence_time": "09:00",
            "recurrence_timezone": "UTC",
            "recurrence_freq": "DAILY",
            "recurrence_interval": 1,
            "generate_count": 1,
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert title in resp.text

    # Ensure it exists in the DB for Alice.
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

    # Logout + login as Bob: Alice's series should not be visible.
    await client.post("/logout", follow_redirects=True)
    await _login(client, "bob@example.com", "pw-bob")

    resp = await client.get("/")
    assert resp.status_code == 200
    assert title not in resp.text

    # Bob also should not be able to view Alice's series detail.
    resp = await client.get(f"/series/{series.id}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_create_series_generates_occurrences(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _login(client, "alice@example.com", "pw-alice")

    title = f"Generate {uuid.uuid4()}"
    resp = await client.post(
        "/series",
        data={
            "title": title,
            "reminder_minutes_before": 60,
            "recurrence_start_date": "2030-01-01",
            "recurrence_time": "09:00",
            "recurrence_timezone": "UTC",
            "recurrence_freq": "DAILY",
            "recurrence_interval": 1,
            "generate_count": 3,
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

    assert len(occs) == 3


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@pytest.mark.asyncio
async def test_create_series_auto_creates_email_reminders(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _login(client, "alice@example.com", "pw-alice")

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


@pytest.mark.asyncio
async def test_manual_occurrence_creation_auto_creates_email_reminder(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _login(client, "alice@example.com", "pw-alice")

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

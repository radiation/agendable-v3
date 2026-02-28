from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import (
    MeetingOccurrence,
    MeetingOccurrenceAttendee,
    MeetingSeries,
    User,
)
from agendable.testing.web_test_helpers import login_user


@pytest.mark.asyncio
async def test_create_series_adds_entered_attendees_to_generated_occurrences(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await login_user(client, "teammate@example.com", "pw-teammate")
    await client.post("/logout", follow_redirects=True)
    await login_user(client, "alice@example.com", "pw-alice")

    title = f"Create attendee emails {uuid.uuid4()}"
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
            "attendee_emails": "teammate@example.com",
            "generate_count": 2,
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    alice = (
        await db_session.execute(select(User).where(User.email == "alice@example.com"))
    ).scalar_one()
    teammate = (
        await db_session.execute(select(User).where(User.email == "teammate@example.com"))
    ).scalar_one()
    series = (
        await db_session.execute(
            select(MeetingSeries).where(
                MeetingSeries.owner_user_id == alice.id,
                MeetingSeries.title == title,
            )
        )
    ).scalar_one()

    occurrences = (
        (
            await db_session.execute(
                select(MeetingOccurrence.id).where(MeetingOccurrence.series_id == series.id)
            )
        )
        .scalars()
        .all()
    )
    teammate_links = (
        (
            await db_session.execute(
                select(MeetingOccurrenceAttendee).where(
                    MeetingOccurrenceAttendee.user_id == teammate.id,
                    MeetingOccurrenceAttendee.occurrence_id.in_(occurrences),
                )
            )
        )
        .scalars()
        .all()
    )

    assert len(teammate_links) == len(occurrences) == 2


async def test_create_series_and_list_is_scoped_to_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    title = f"1:1 {uuid.uuid4()}"

    await login_user(client, "alice@example.com", "pw-alice")

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
    await login_user(client, "bob@example.com", "pw-bob")

    resp = await client.get("/")
    assert resp.status_code == 200
    assert title not in resp.text

    # Bob also should not be able to view Alice's series detail.
    resp = await client.get(f"/series/{series.id}")
    assert resp.status_code == 404


async def test_create_series_auto_adds_owner_as_attendee_to_generated_occurrences(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    title = f"Owner attendee {uuid.uuid4()}"
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

    occurrences = (
        (
            await db_session.execute(
                select(MeetingOccurrence.id).where(MeetingOccurrence.series_id == series.id)
            )
        )
        .scalars()
        .all()
    )
    owner_links = (
        (
            await db_session.execute(
                select(MeetingOccurrenceAttendee).where(
                    MeetingOccurrenceAttendee.user_id == alice.id,
                    MeetingOccurrenceAttendee.occurrence_id.in_(occurrences),
                )
            )
        )
        .scalars()
        .all()
    )

    assert len(owner_links) == len(occurrences) == 2


async def test_create_series_generates_occurrences(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

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


async def test_create_series_rejects_unknown_attendee_email(client: AsyncClient) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    resp = await client.post(
        "/series",
        data={
            "title": "Unknown attendee",
            "reminder_minutes_before": 60,
            "recurrence_start_date": "2030-01-01",
            "recurrence_time": "09:00",
            "recurrence_timezone": "UTC",
            "recurrence_freq": "DAILY",
            "recurrence_interval": 1,
            "attendee_emails": "unknown@example.com",
            "generate_count": 1,
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Unknown attendee email(s): unknown@example.com"


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

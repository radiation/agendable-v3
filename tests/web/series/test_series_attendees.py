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
async def test_add_series_attendee_404_when_series_not_owned(client: AsyncClient) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    resp = await client.post(
        f"/series/{uuid.uuid4()}/attendees",
        data={"email": "bob@example.com"},
        follow_redirects=False,
    )

    assert resp.status_code == 404


async def test_add_series_attendee_adds_to_all_occurrences_and_is_idempotent(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    title = f"Series attendees {uuid.uuid4()}"
    create_resp = await client.post(
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

    first = await client.post(
        f"/series/{series.id}/attendees",
        data={"email": "alice@example.com"},
        follow_redirects=False,
    )
    assert first.status_code == 303

    second = await client.post(
        f"/series/{series.id}/attendees",
        data={"email": "alice@example.com"},
        follow_redirects=False,
    )
    assert second.status_code == 303

    occurrence_count = (
        (
            await db_session.execute(
                select(MeetingOccurrence.id).where(MeetingOccurrence.series_id == series.id)
            )
        )
        .scalars()
        .all()
    )
    links = (
        (
            await db_session.execute(
                select(MeetingOccurrenceAttendee).where(
                    MeetingOccurrenceAttendee.user_id == alice.id,
                    MeetingOccurrenceAttendee.occurrence_id.in_(occurrence_count),
                )
            )
        )
        .scalars()
        .all()
    )

    assert len(links) == len(occurrence_count) == 3


async def test_add_series_attendee_shows_inline_validation_error(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    title = f"Series attendee validation {uuid.uuid4()}"
    create_resp = await client.post(
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
        f"/series/{series.id}/attendees",
        data={"email": "nobody@example.com"},
        follow_redirects=False,
    )

    assert resp.status_code == 400
    assert "No user found with that email." in resp.text
    assert 'value="nobody@example.com"' in resp.text

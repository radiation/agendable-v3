from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import (
    MeetingSeries,
    User,
)
from agendable.testing.web_test_helpers import login_user


@pytest.mark.asyncio
async def test_series_attendee_suggestions_ignores_short_queries(client: AsyncClient) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    resp = await client.get("/series/attendee-suggestions?q=a")
    assert resp.status_code == 200
    assert resp.text == ""


async def test_series_attendee_suggestions_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/series/attendee-suggestions?q=al")
    assert resp.status_code == 401


async def test_series_attendee_suggestions_returns_matching_users(client: AsyncClient) -> None:
    await login_user(
        client, "teammate@example.com", "pw-teammate", first_name="Team", last_name="Mate"
    )
    await client.post("/logout", follow_redirects=True)
    await login_user(client, "alice@example.com", "pw-alice")

    resp = await client.get("/series/attendee-suggestions?q=team")
    assert resp.status_code == 200
    assert "teammate@example.com" in resp.text
    assert "alice@example.com" not in resp.text


async def test_series_attendee_suggestions_uses_last_attendee_token(client: AsyncClient) -> None:
    await login_user(
        client, "teammate@example.com", "pw-teammate", first_name="Team", last_name="Mate"
    )
    await client.post("/logout", follow_redirects=True)
    await login_user(client, "alice@example.com", "pw-alice")

    resp = await client.get(
        "/series/attendee-suggestions",
        params={"attendee_emails": "first@example.com, tea"},
    )
    assert resp.status_code == 200
    assert "teammate@example.com" in resp.text


async def test_series_detail_handles_all_past_occurrences(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    title = f"Past series {uuid.uuid4()}"
    resp = await client.post(
        "/series",
        data={
            "title": title,
            "reminder_minutes_before": 60,
            "recurrence_start_date": "2000-01-01",
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

    detail = await client.get(f"/series/{series.id}")
    assert detail.status_code == 200
    assert title in detail.text


async def test_series_recurrence_options_renders_mode_specific_controls(
    client: AsyncClient,
) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    weekly = await client.get("/series/recurrence-options?recurrence_freq=WEEKLY")
    assert weekly.status_code == 200
    assert "Weekly days" in weekly.text
    assert "monthly_bymonthday" not in weekly.text

    monthly = await client.get("/series/recurrence-options?recurrence_freq=MONTHLY")
    assert monthly.status_code == 200
    assert "monthly_bymonthday" in monthly.text
    assert "Weekly days" not in monthly.text

    fallback = await client.get("/series/recurrence-options?recurrence_freq=UNKNOWN")
    assert fallback.status_code == 200
    assert "Daily recurrence does not require extra options." in fallback.text


async def test_series_recurrence_options_requires_auth(client: AsyncClient) -> None:
    resp = await client.get("/series/recurrence-options?recurrence_freq=WEEKLY")
    assert resp.status_code == 401

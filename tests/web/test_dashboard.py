from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import MeetingOccurrence, MeetingSeries, Task, User


async def _login(client: AsyncClient, email: str, password: str) -> None:
    resp = await client.post(
        "/signup",
        data={
            "first_name": "Dash",
            "last_name": "User",
            "timezone": "UTC",
            "email": email,
            "password": password,
        },
        follow_redirects=True,
    )
    if resp.status_code == 200:
        return

    resp = await client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=True,
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_dashboard_shows_upcoming_and_tasks_ordered_by_due_date(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _login(client, "dash-owner@example.com", "pw-dash")

    owner = (
        await db_session.execute(select(User).where(User.email == "dash-owner@example.com"))
    ).scalar_one()

    series = MeetingSeries(
        owner_user_id=owner.id, title=f"Dash Series {uuid.uuid4()}", default_interval_days=7
    )
    db_session.add(series)
    await db_session.flush()

    now = datetime.now(UTC)
    upcoming_1 = MeetingOccurrence(
        series_id=series.id, scheduled_at=now + timedelta(days=1), notes=""
    )
    upcoming_2 = MeetingOccurrence(
        series_id=series.id, scheduled_at=now + timedelta(days=3), notes=""
    )
    past = MeetingOccurrence(series_id=series.id, scheduled_at=now - timedelta(days=2), notes="")
    db_session.add_all([upcoming_1, upcoming_2, past])
    await db_session.flush()

    later_due = Task(
        occurrence_id=upcoming_1.id,
        assigned_user_id=owner.id,
        title="Later due",
        due_at=now + timedelta(days=5),
        is_done=False,
    )
    earlier_due = Task(
        occurrence_id=upcoming_2.id,
        assigned_user_id=owner.id,
        title="Earlier due",
        due_at=now + timedelta(days=2),
        is_done=False,
    )
    done_task = Task(
        occurrence_id=upcoming_2.id,
        assigned_user_id=owner.id,
        title="Done task",
        due_at=now + timedelta(days=1),
        is_done=True,
    )
    db_session.add_all([later_due, earlier_due, done_task])
    await db_session.commit()

    resp = await client.get("/dashboard")
    assert resp.status_code == 200

    assert str(upcoming_1.id) in resp.text
    assert str(upcoming_2.id) in resp.text
    assert str(past.id) not in resp.text

    earlier_idx = resp.text.index("Earlier due")
    later_idx = resp.text.index("Later due")
    assert earlier_idx < later_idx
    assert "Done task" not in resp.text

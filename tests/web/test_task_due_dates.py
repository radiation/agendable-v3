from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import MeetingOccurrence, Task
from agendable.testing.web_test_helpers import create_series, login_user


@pytest.mark.asyncio
async def test_task_defaults_due_at_to_next_occurrence_when_available(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login_user(client, "alice@example.com", "pw-alice")
    series = await create_series(
        client,
        db_session,
        owner_email="alice@example.com",
        title=f"DueDefault {uuid.uuid4()}",
    )

    first = (
        (
            await db_session.execute(
                select(MeetingOccurrence)
                .where(MeetingOccurrence.series_id == series.id)
                .order_by(MeetingOccurrence.scheduled_at.asc())
            )
        )
        .scalars()
        .first()
    )
    assert first is not None

    second = MeetingOccurrence(
        series_id=series.id,
        scheduled_at=first.scheduled_at + timedelta(days=2),
        notes="",
    )
    db_session.add(second)
    await db_session.commit()
    await db_session.refresh(second)

    resp = await client.post(
        f"/occurrences/{first.id}/tasks",
        data={"title": "Default due"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    created = (
        await db_session.execute(
            select(Task).where(Task.occurrence_id == first.id, Task.title == "Default due")
        )
    ).scalar_one()
    assert created.due_at == second.scheduled_at


@pytest.mark.asyncio
async def test_task_due_at_can_be_overridden_from_form(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login_user(client, "alice@example.com", "pw-alice")
    series = await create_series(
        client,
        db_session,
        owner_email="alice@example.com",
        title=f"DueOverride {uuid.uuid4()}",
    )

    first = (
        (
            await db_session.execute(
                select(MeetingOccurrence)
                .where(MeetingOccurrence.series_id == series.id)
                .order_by(MeetingOccurrence.scheduled_at.asc())
            )
        )
        .scalars()
        .first()
    )
    assert first is not None

    second = MeetingOccurrence(
        series_id=series.id,
        scheduled_at=first.scheduled_at + timedelta(days=2),
        notes="",
    )
    db_session.add(second)
    await db_session.commit()
    await db_session.refresh(second)

    override_due = first.scheduled_at + timedelta(hours=6)
    override_due_form = override_due.strftime("%Y-%m-%dT%H:%M")

    resp = await client.post(
        f"/occurrences/{first.id}/tasks",
        data={"title": "Custom due", "due_at": override_due_form},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    created = (
        await db_session.execute(
            select(Task).where(Task.occurrence_id == first.id, Task.title == "Custom due")
        )
    ).scalar_one()
    assert created.due_at == override_due.replace(second=0, microsecond=0)


@pytest.mark.asyncio
async def test_task_default_due_at_uses_next_active_occurrence(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login_user(client, "alice@example.com", "pw-alice")
    series = await create_series(
        client,
        db_session,
        owner_email="alice@example.com",
        title=f"DueNextActive {uuid.uuid4()}",
    )

    first = (
        (
            await db_session.execute(
                select(MeetingOccurrence)
                .where(MeetingOccurrence.series_id == series.id)
                .order_by(MeetingOccurrence.scheduled_at.asc())
            )
        )
        .scalars()
        .first()
    )
    assert first is not None

    completed_next = MeetingOccurrence(
        series_id=series.id,
        scheduled_at=first.scheduled_at + timedelta(days=1),
        notes="",
        is_completed=True,
    )
    active_next = MeetingOccurrence(
        series_id=series.id,
        scheduled_at=first.scheduled_at + timedelta(days=7),
        notes="",
        is_completed=False,
    )
    db_session.add_all([completed_next, active_next])
    await db_session.commit()

    resp = await client.post(
        f"/occurrences/{first.id}/tasks",
        data={"title": "Default to active next"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    created = (
        await db_session.execute(
            select(Task).where(
                Task.occurrence_id == first.id,
                Task.title == "Default to active next",
            )
        )
    ).scalar_one()
    assert created.due_at == active_next.scheduled_at

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

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


@pytest.mark.asyncio
async def test_task_due_default_value_is_prepopulated_in_user_timezone(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login_user(
        client,
        "alice-ny@example.com",
        "pw-alice",
        timezone="America/New_York",
    )
    series = await create_series(
        client,
        db_session,
        owner_email="alice-ny@example.com",
        title=f"DueLocalValue {uuid.uuid4()}",
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

    response = await client.get(f"/occurrences/{first.id}")
    assert response.status_code == 200
    assert 'name="due_at" type="datetime-local" value="2030-01-01T04:00"' in response.text


@pytest.mark.asyncio
async def test_task_due_override_is_interpreted_in_user_timezone(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login_user(
        client,
        "alice-ny-override@example.com",
        "pw-alice",
        timezone="America/New_York",
    )
    series = await create_series(
        client,
        db_session,
        owner_email="alice-ny-override@example.com",
        title=f"DueLocalOverride {uuid.uuid4()}",
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

    response = await client.post(
        f"/occurrences/{first.id}/tasks",
        data={"title": "Local override", "due_at": "2030-01-01T16:00"},
        follow_redirects=False,
    )
    assert response.status_code == 303

    created = (
        await db_session.execute(
            select(Task).where(Task.occurrence_id == first.id, Task.title == "Local override")
        )
    ).scalar_one()
    assert created.due_at.replace(tzinfo=UTC) == datetime(2030, 1, 1, 21, 0, tzinfo=UTC)


@pytest.mark.asyncio
async def test_task_due_blank_string_falls_back_to_default_due(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login_user(client, "alice-blank-due@example.com", "pw-alice")
    series = await create_series(
        client,
        db_session,
        owner_email="alice-blank-due@example.com",
        title=f"DueBlankDefault {uuid.uuid4()}",
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
        scheduled_at=first.scheduled_at + timedelta(days=3),
        notes="",
    )
    db_session.add(second)
    await db_session.commit()

    response = await client.post(
        f"/occurrences/{first.id}/tasks",
        data={"title": "Blank due", "due_at": "   "},
        follow_redirects=False,
    )
    assert response.status_code == 303

    created = (
        await db_session.execute(
            select(Task).where(Task.occurrence_id == first.id, Task.title == "Blank due")
        )
    ).scalar_one()
    assert created.due_at == second.scheduled_at


@pytest.mark.asyncio
async def test_task_due_invalid_value_returns_400(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login_user(client, "alice-invalid-due@example.com", "pw-alice")
    series = await create_series(
        client,
        db_session,
        owner_email="alice-invalid-due@example.com",
        title=f"DueInvalid {uuid.uuid4()}",
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

    response = await client.post(
        f"/occurrences/{first.id}/tasks",
        data={"title": "Invalid due", "due_at": "not-a-datetime"},
        follow_redirects=False,
    )
    assert response.status_code == 400

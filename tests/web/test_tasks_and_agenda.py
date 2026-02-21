from __future__ import annotations

import uuid
from datetime import timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import agendable.db as db
from agendable.db.models import AgendaItem, MeetingOccurrence, MeetingSeries, Task, User


async def _login(client: AsyncClient, email: str, password: str) -> None:
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

    resp = await client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=True,
    )
    assert resp.status_code == 200


async def _create_series(
    client: AsyncClient, db_session: AsyncSession, title: str
) -> MeetingSeries:
    resp = await client.post(
        "/series",
        data={
            "title": title,
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

    user = (
        await db_session.execute(select(User).where(User.email == "alice@example.com"))
    ).scalar_one()
    return (
        await db_session.execute(
            select(MeetingSeries).where(
                MeetingSeries.owner_user_id == user.id, MeetingSeries.title == title
            )
        )
    ).scalar_one()


@pytest.mark.asyncio
async def test_task_create_and_toggle_is_scoped(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _login(client, "alice@example.com", "pw-alice")

    series = await _create_series(client, db_session, title=f"Tasks {uuid.uuid4()}")

    # Use the auto-generated occurrence.
    occ = (
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
    assert occ is not None

    resp = await client.post(
        f"/occurrences/{occ.id}/tasks",
        data={"title": "Do the thing"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    task = (
        await db_session.execute(
            select(Task).where(Task.occurrence_id == occ.id, Task.title == "Do the thing")
        )
    ).scalar_one()
    assert task.is_done is False
    assert task.assigned_user_id == series.owner_user_id
    assert task.due_at == occ.scheduled_at
    task_id = task.id

    resp = await client.post(f"/tasks/{task.id}/toggle", follow_redirects=True)
    assert resp.status_code == 200

    async with db.SessionMaker() as verify_session:
        refreshed = (
            await verify_session.execute(select(Task).where(Task.id == task_id))
        ).scalar_one()
        assert refreshed.is_done is True

    # Other users cannot toggle Alice's tasks.
    await client.post("/logout", follow_redirects=True)
    await _login(client, "bob@example.com", "pw-bob")

    resp = await client.post(f"/tasks/{task.id}/toggle")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_agenda_add_and_toggle_is_scoped(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _login(client, "alice@example.com", "pw-alice")

    series = await _create_series(client, db_session, title=f"Agenda {uuid.uuid4()}")

    # Use the auto-generated occurrence.
    occ = (
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
    assert occ is not None

    resp = await client.post(
        f"/occurrences/{occ.id}/agenda",
        data={"body": "Talk about priorities"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    item = (
        await db_session.execute(
            select(AgendaItem).where(
                AgendaItem.occurrence_id == occ.id, AgendaItem.body == "Talk about priorities"
            )
        )
    ).scalar_one()
    assert item.is_done is False
    item_id = item.id

    resp = await client.post(f"/agenda/{item.id}/toggle", follow_redirects=True)
    assert resp.status_code == 200

    async with db.SessionMaker() as verify_session:
        refreshed = (
            await verify_session.execute(select(AgendaItem).where(AgendaItem.id == item_id))
        ).scalar_one()
        assert refreshed.is_done is True

    # Other users cannot toggle Alice's agenda.
    await client.post("/logout", follow_redirects=True)
    await _login(client, "bob@example.com", "pw-bob")

    resp = await client.post(f"/agenda/{item.id}/toggle")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_complete_occurrence_rolls_unfinished_items_to_next_occurrence(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _login(client, "alice@example.com", "pw-alice")

    series = await _create_series(client, db_session, title=f"Roll {uuid.uuid4()}")

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
        scheduled_at=first.scheduled_at + timedelta(days=1),
        notes="",
    )
    db_session.add(second)
    await db_session.commit()
    await db_session.refresh(second)

    unfinished_task = Task(
        occurrence_id=first.id,
        assigned_user_id=series.owner_user_id,
        due_at=first.scheduled_at,
        title="Move me",
        is_done=False,
    )
    completed_task = Task(
        occurrence_id=first.id,
        assigned_user_id=series.owner_user_id,
        due_at=first.scheduled_at,
        title="Keep me",
        is_done=True,
    )
    unfinished_agenda = AgendaItem(occurrence_id=first.id, body="Move agenda", is_done=False)
    completed_agenda = AgendaItem(occurrence_id=first.id, body="Keep agenda", is_done=True)
    db_session.add_all([unfinished_task, completed_task, unfinished_agenda, completed_agenda])
    await db_session.commit()

    resp = await client.post(f"/occurrences/{first.id}/complete", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == f"/occurrences/{second.id}"

    async with db.SessionMaker() as verify_session:
        refreshed_first = (
            await verify_session.execute(
                select(MeetingOccurrence).where(MeetingOccurrence.id == first.id)
            )
        ).scalar_one()
        assert refreshed_first.is_completed is True

        moved_task = (
            await verify_session.execute(select(Task).where(Task.id == unfinished_task.id))
        ).scalar_one()
        kept_task = (
            await verify_session.execute(select(Task).where(Task.id == completed_task.id))
        ).scalar_one()
        assert moved_task.occurrence_id == second.id
        assert kept_task.occurrence_id == first.id
        assert moved_task.due_at == second.scheduled_at
        assert kept_task.due_at == first.scheduled_at

        moved_agenda = (
            await verify_session.execute(
                select(AgendaItem).where(AgendaItem.id == unfinished_agenda.id)
            )
        ).scalar_one()
        kept_agenda = (
            await verify_session.execute(
                select(AgendaItem).where(AgendaItem.id == completed_agenda.id)
            )
        ).scalar_one()
        assert moved_agenda.occurrence_id == second.id
        assert kept_agenda.occurrence_id == first.id


@pytest.mark.asyncio
async def test_task_assignment_requires_attendee(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _login(client, "alice@example.com", "pw-alice")

    series = await _create_series(client, db_session, title=f"Assign {uuid.uuid4()}")

    occ = (
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
    assert occ is not None

    bob = User(
        email=f"bob-{uuid.uuid4()}@example.com",
        first_name="Bob",
        last_name="Builder",
        display_name="Bob Builder",
        timezone="UTC",
        password_hash=None,
    )
    db_session.add(bob)
    await db_session.commit()
    await db_session.refresh(bob)

    resp = await client.post(
        f"/occurrences/{occ.id}/tasks",
        data={"title": "Assigned task", "assigned_user_id": str(bob.id)},
        follow_redirects=False,
    )
    assert resp.status_code == 400

    resp = await client.post(
        f"/occurrences/{occ.id}/attendees",
        data={"email": bob.email},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    resp = await client.post(
        f"/occurrences/{occ.id}/tasks",
        data={"title": "Assigned task", "assigned_user_id": str(bob.id)},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    assigned_task = (
        await db_session.execute(
            select(Task).where(Task.occurrence_id == occ.id, Task.title == "Assigned task")
        )
    ).scalar_one()
    assert assigned_task.assigned_user_id == bob.id


@pytest.mark.asyncio
async def test_completed_occurrence_is_read_only(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _login(client, "alice@example.com", "pw-alice")
    series = await _create_series(client, db_session, title=f"ReadOnly {uuid.uuid4()}")

    occ = (
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
    assert occ is not None

    task = Task(
        occurrence_id=occ.id,
        assigned_user_id=series.owner_user_id,
        due_at=occ.scheduled_at,
        title="Existing task",
        is_done=False,
    )
    agenda = AgendaItem(occurrence_id=occ.id, body="Existing agenda", is_done=False)
    bob = User(
        email=f"readonly-bob-{uuid.uuid4()}@example.com",
        first_name="Bob",
        last_name="Readonly",
        display_name="Bob Readonly",
        timezone="UTC",
        password_hash=None,
    )
    db_session.add_all([task, agenda, bob])
    await db_session.commit()
    await db_session.refresh(task)
    await db_session.refresh(agenda)
    await db_session.refresh(bob)

    resp = await client.post(f"/occurrences/{occ.id}/complete", follow_redirects=False)
    assert resp.status_code == 303

    add_task_resp = await client.post(
        f"/occurrences/{occ.id}/tasks",
        data={"title": "Should fail"},
        follow_redirects=False,
    )
    assert add_task_resp.status_code == 400

    add_agenda_resp = await client.post(
        f"/occurrences/{occ.id}/agenda",
        data={"body": "Should fail"},
        follow_redirects=False,
    )
    assert add_agenda_resp.status_code == 400

    add_attendee_resp = await client.post(
        f"/occurrences/{occ.id}/attendees",
        data={"email": bob.email},
        follow_redirects=False,
    )
    assert add_attendee_resp.status_code == 400

    toggle_task_resp = await client.post(f"/tasks/{task.id}/toggle", follow_redirects=False)
    assert toggle_task_resp.status_code == 400

    toggle_agenda_resp = await client.post(f"/agenda/{agenda.id}/toggle", follow_redirects=False)
    assert toggle_agenda_resp.status_code == 400

    async with db.SessionMaker() as verify_session:
        refreshed_occ = (
            await verify_session.execute(
                select(MeetingOccurrence).where(MeetingOccurrence.id == occ.id)
            )
        ).scalar_one()
        assert refreshed_occ.is_completed is True

        refreshed_task = (
            await verify_session.execute(select(Task).where(Task.id == task.id))
        ).scalar_one()
        refreshed_agenda = (
            await verify_session.execute(select(AgendaItem).where(AgendaItem.id == agenda.id))
        ).scalar_one()
        assert refreshed_task.is_done is False
        assert refreshed_agenda.is_done is False

        still_one_task = (
            (
                await verify_session.execute(
                    select(Task).where(Task.occurrence_id == occ.id, Task.title == "Existing task")
                )
            )
            .scalars()
            .all()
        )
        still_one_agenda = (
            (
                await verify_session.execute(
                    select(AgendaItem).where(
                        AgendaItem.occurrence_id == occ.id,
                        AgendaItem.body == "Existing agenda",
                    )
                )
            )
            .scalars()
            .all()
        )
        assert len(still_one_task) == 1
        assert len(still_one_agenda) == 1


@pytest.mark.asyncio
async def test_task_defaults_due_at_to_next_occurrence_when_available(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await _login(client, "alice@example.com", "pw-alice")
    series = await _create_series(client, db_session, title=f"DueDefault {uuid.uuid4()}")

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

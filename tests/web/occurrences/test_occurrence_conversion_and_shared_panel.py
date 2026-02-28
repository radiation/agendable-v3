from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import agendable.db as db
from agendable.db.models import AgendaItem, MeetingOccurrence, Task, User
from agendable.testing.web_test_helpers import create_series, login_user


@pytest.mark.asyncio
async def test_convert_agenda_item_to_task_assigns_attendee_and_marks_done(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    series = await create_series(
        client,
        db_session,
        owner_email="alice@example.com",
        title=f"Convert Agenda {uuid.uuid4()}",
    )

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
        email=f"convert-bob-{uuid.uuid4()}@example.com",
        first_name="Bob",
        last_name="Convert",
        display_name="Bob Convert",
        timezone="UTC",
        password_hash=None,
    )
    db_session.add(bob)
    await db_session.commit()
    await db_session.refresh(bob)

    add_attendee_resp = await client.post(
        f"/occurrences/{occ.id}/attendees",
        data={"email": bob.email},
        follow_redirects=False,
    )
    assert add_attendee_resp.status_code == 303

    agenda = AgendaItem(
        occurrence_id=occ.id,
        body="Discuss migration plan",
        description="Break it into milestones",
        is_done=False,
    )
    db_session.add(agenda)
    await db_session.commit()
    await db_session.refresh(agenda)

    convert_resp = await client.post(
        f"/agenda/{agenda.id}/convert-to-task",
        data={"assigned_user_id": str(bob.id)},
        follow_redirects=False,
    )
    assert convert_resp.status_code == 303

    converted_task = (
        await db_session.execute(
            select(Task).where(
                Task.occurrence_id == occ.id,
                Task.title == "Discuss migration plan",
            )
        )
    ).scalar_one()
    assert converted_task.assigned_user_id == bob.id
    assert converted_task.description == "Break it into milestones"

    async with db.SessionMaker() as verify_session:
        refreshed_agenda = (
            await verify_session.execute(select(AgendaItem).where(AgendaItem.id == agenda.id))
        ).scalar_one()
        assert refreshed_agenda.is_done is True


async def test_convert_agenda_item_to_task_rejects_non_attendee_assignee(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    series = await create_series(
        client,
        db_session,
        owner_email="alice@example.com",
        title=f"Convert Guardrail {uuid.uuid4()}",
    )

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

    outsider = User(
        email=f"outsider-{uuid.uuid4()}@example.com",
        first_name="Out",
        last_name="Sider",
        display_name="Out Sider",
        timezone="UTC",
        password_hash=None,
    )
    db_session.add(outsider)

    agenda = AgendaItem(occurrence_id=occ.id, body="Should not convert", is_done=False)
    db_session.add(agenda)
    await db_session.commit()
    await db_session.refresh(outsider)
    await db_session.refresh(agenda)

    convert_resp = await client.post(
        f"/agenda/{agenda.id}/convert-to-task",
        data={"assigned_user_id": str(outsider.id)},
        follow_redirects=False,
    )
    assert convert_resp.status_code == 400
    assert convert_resp.json()["detail"] == "Assignee must be a meeting attendee."

    created_tasks = (
        (
            await db_session.execute(
                select(Task).where(
                    Task.occurrence_id == occ.id,
                    Task.title == "Should not convert",
                )
            )
        )
        .scalars()
        .all()
    )
    assert created_tasks == []


async def test_occurrence_shared_panel_renders_live_sections(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    series = await create_series(
        client,
        db_session,
        owner_email="alice@example.com",
        title=f"Shared panel {uuid.uuid4()}",
    )

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
        title="Shared task",
        description="Shared task details",
        due_at=occ.scheduled_at,
        is_done=False,
    )
    agenda = AgendaItem(
        occurrence_id=occ.id,
        body="Shared agenda",
        description="Shared agenda details",
        is_done=False,
    )
    db_session.add_all([task, agenda])
    await db_session.commit()

    resp = await client.get(f"/occurrences/{occ.id}/shared-panel")
    assert resp.status_code == 200
    assert "Last refreshed:" in resp.text
    assert "Active viewers (30s):" in resp.text
    assert "Last activity:" in resp.text
    assert "Attendees, tasks, and agenda stay synced in the sections below." in resp.text
    assert 'hx-swap-oob="outerHTML"' in resp.text
    assert "occurrence-live-tasks" in resp.text
    assert "occurrence-live-agenda" in resp.text
    assert 'data-live-key="task-' in resp.text
    assert 'data-live-signature="' in resp.text
    assert 'data-live-key="agenda-' in resp.text


async def test_occurrence_shared_panel_is_scoped_to_owner(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    series = await create_series(
        client,
        db_session,
        owner_email="alice@example.com",
        title=f"Shared panel scope {uuid.uuid4()}",
    )

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

    await client.post("/logout", follow_redirects=True)
    await login_user(client, "bob@example.com", "pw-bob")

    resp = await client.get(f"/occurrences/{occ.id}/shared-panel")
    assert resp.status_code == 404

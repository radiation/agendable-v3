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
async def test_invited_attendee_can_view_occurrence_pages(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await login_user(client, "view-bob@example.com", "pw-bob")
    await client.post("/logout", follow_redirects=True)

    await login_user(client, "alice@example.com", "pw-alice")

    series = await create_series(
        client,
        db_session,
        owner_email="alice@example.com",
        title=f"Invite view {uuid.uuid4()}",
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

    bob = (
        await db_session.execute(select(User).where(User.email == "view-bob@example.com"))
    ).scalar_one()

    add_attendee = await client.post(
        f"/occurrences/{occ.id}/attendees",
        data={"email": bob.email},
        follow_redirects=False,
    )
    assert add_attendee.status_code == 303

    await client.post("/logout", follow_redirects=True)
    await login_user(client, bob.email, "pw-bob")

    detail_resp = await client.get(f"/occurrences/{occ.id}")
    assert detail_resp.status_code == 200
    assert "Shared meeting view" in detail_resp.text
    assert "Up to date" in detail_resp.text
    assert "Shortcuts: Cmd/Ctrl+K focuses task" in detail_resp.text
    assert 'id="task-capture-form"' in detail_resp.text
    assert 'id="agenda-capture-form"' in detail_resp.text

    shared_panel_resp = await client.get(f"/occurrences/{occ.id}/shared-panel")
    assert shared_panel_resp.status_code == 200
    assert "Last refreshed:" in shared_panel_resp.text
    assert "Active viewers (30s):" in shared_panel_resp.text
    assert "Last activity:" in shared_panel_resp.text


async def test_invited_attendee_can_create_task_and_agenda_item(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await login_user(client, "shared-bob@example.com", "pw-bob")
    await client.post("/logout", follow_redirects=True)

    await login_user(client, "alice@example.com", "pw-alice")

    series = await create_series(
        client,
        db_session,
        owner_email="alice@example.com",
        title=f"Invite mutate {uuid.uuid4()}",
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

    bob = (
        await db_session.execute(select(User).where(User.email == "shared-bob@example.com"))
    ).scalar_one()

    add_attendee = await client.post(
        f"/occurrences/{occ.id}/attendees",
        data={"email": bob.email},
        follow_redirects=False,
    )
    assert add_attendee.status_code == 303

    await client.post("/logout", follow_redirects=True)
    await login_user(client, bob.email, "pw-bob")

    add_task_resp = await client.post(
        f"/occurrences/{occ.id}/tasks",
        data={"title": "Bob task", "description": "Created by attendee"},
        follow_redirects=False,
    )
    assert add_task_resp.status_code == 303

    add_agenda_resp = await client.post(
        f"/occurrences/{occ.id}/agenda",
        data={"body": "Bob agenda", "description": "Created by attendee"},
        follow_redirects=False,
    )
    assert add_agenda_resp.status_code == 303

    created_task = (
        await db_session.execute(
            select(Task).where(Task.occurrence_id == occ.id, Task.title == "Bob task")
        )
    ).scalar_one()
    assert created_task.description == "Created by attendee"

    created_agenda = (
        await db_session.execute(
            select(AgendaItem).where(
                AgendaItem.occurrence_id == occ.id, AgendaItem.body == "Bob agenda"
            )
        )
    ).scalar_one()
    assert created_agenda.description == "Created by attendee"


async def test_invited_attendee_can_toggle_and_convert_items(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await login_user(client, "collab-bob@example.com", "pw-bob")
    await client.post("/logout", follow_redirects=True)

    await login_user(client, "alice@example.com", "pw-alice")

    series = await create_series(
        client,
        db_session,
        owner_email="alice@example.com",
        title=f"Invite collab mutate {uuid.uuid4()}",
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

    bob = (
        await db_session.execute(select(User).where(User.email == "collab-bob@example.com"))
    ).scalar_one()

    add_attendee = await client.post(
        f"/occurrences/{occ.id}/attendees",
        data={"email": bob.email},
        follow_redirects=False,
    )
    assert add_attendee.status_code == 303

    owner_task = Task(
        occurrence_id=occ.id,
        assigned_user_id=series.owner_user_id,
        due_at=occ.scheduled_at,
        title="Owner task",
        is_done=False,
    )
    owner_agenda = AgendaItem(occurrence_id=occ.id, body="Owner agenda", is_done=False)
    convert_agenda = AgendaItem(occurrence_id=occ.id, body="Convert me", is_done=False)
    db_session.add_all([owner_task, owner_agenda, convert_agenda])
    await db_session.commit()
    await db_session.refresh(owner_task)
    await db_session.refresh(owner_agenda)
    await db_session.refresh(convert_agenda)

    await client.post("/logout", follow_redirects=True)
    await login_user(client, bob.email, "pw-bob")

    toggle_task_resp = await client.post(
        f"/tasks/{owner_task.id}/toggle",
        follow_redirects=False,
    )
    assert toggle_task_resp.status_code == 303

    toggle_agenda_resp = await client.post(
        f"/agenda/{owner_agenda.id}/toggle",
        follow_redirects=False,
    )
    assert toggle_agenda_resp.status_code == 303

    convert_resp = await client.post(
        f"/agenda/{convert_agenda.id}/convert-to-task",
        data={"assigned_user_id": str(bob.id)},
        follow_redirects=False,
    )
    assert convert_resp.status_code == 303

    async with db.SessionMaker() as verify_session:
        toggled_task = (
            await verify_session.execute(select(Task).where(Task.id == owner_task.id))
        ).scalar_one()
        toggled_agenda = (
            await verify_session.execute(select(AgendaItem).where(AgendaItem.id == owner_agenda.id))
        ).scalar_one()
        converted_agenda = (
            await verify_session.execute(
                select(AgendaItem).where(AgendaItem.id == convert_agenda.id)
            )
        ).scalar_one()
        converted_task = (
            await verify_session.execute(
                select(Task).where(Task.occurrence_id == occ.id, Task.title == "Convert me")
            )
        ).scalar_one()

        assert toggled_task.is_done is True
        assert toggled_agenda.is_done is True
        assert converted_agenda.is_done is True
        assert converted_task.assigned_user_id == bob.id

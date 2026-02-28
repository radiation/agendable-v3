from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import MeetingOccurrence
from agendable.testing.web_test_helpers import create_series, login_user


@pytest.mark.asyncio
async def test_task_form_shows_inline_validation_errors(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    series = await create_series(
        client,
        db_session,
        owner_email="alice@example.com",
        title=f"Task UX {uuid.uuid4()}",
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

    resp = await client.post(
        f"/occurrences/{occ.id}/tasks",
        data={"title": "   ", "due_at": "not-a-datetime"},
        follow_redirects=False,
    )

    assert resp.status_code == 400
    assert "Task title is required." in resp.text
    assert "Enter a valid due date and time." in resp.text


async def test_agenda_form_shows_inline_validation_errors(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    series = await create_series(
        client,
        db_session,
        owner_email="alice@example.com",
        title=f"Agenda UX {uuid.uuid4()}",
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

    resp = await client.post(
        f"/occurrences/{occ.id}/agenda",
        data={"body": "   "},
        follow_redirects=False,
    )

    assert resp.status_code == 400
    assert "Agenda item is required." in resp.text


async def test_attendee_form_shows_inline_validation_for_unknown_email(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    series = await create_series(
        client,
        db_session,
        owner_email="alice@example.com",
        title=f"Attendee UX {uuid.uuid4()}",
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

    resp = await client.post(
        f"/occurrences/{occ.id}/attendees",
        data={"email": "nobody@example.com"},
        follow_redirects=False,
    )

    assert resp.status_code == 400
    assert "No user found with that email." in resp.text
    assert 'value="nobody@example.com"' in resp.text


async def test_attendee_form_shows_inline_validation_for_blank_email(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    series = await create_series(
        client,
        db_session,
        owner_email="alice@example.com",
        title=f"Attendee Blank UX {uuid.uuid4()}",
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

    resp = await client.post(
        f"/occurrences/{occ.id}/attendees",
        data={"email": "   "},
        follow_redirects=False,
    )

    assert resp.status_code == 400
    assert "Attendee email is required." in resp.text

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import MeetingOccurrence, MeetingSeries, Reminder, ReminderChannel, User
from agendable.testing.web_test_helpers import login_user
from agendable.web.routes import series as series_routes


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


@pytest.mark.asyncio
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


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


@pytest.mark.asyncio
async def test_create_series_auto_creates_email_reminders(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    title = f"Reminders {uuid.uuid4()}"
    resp = await client.post(
        "/series",
        data={
            "title": title,
            "reminder_minutes_before": 120,
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
    reminders = (
        (
            await db_session.execute(
                select(Reminder)
                .join(MeetingOccurrence, Reminder.occurrence_id == MeetingOccurrence.id)
                .where(MeetingOccurrence.series_id == series.id)
                .order_by(MeetingOccurrence.scheduled_at.asc())
            )
        )
        .scalars()
        .all()
    )

    assert len(reminders) == len(occs) == 2
    for occ, reminder in zip(occs, reminders, strict=True):
        assert reminder.channel == ReminderChannel.email
        assert _as_utc(reminder.send_at) == _as_utc(occ.scheduled_at) - timedelta(minutes=120)


@pytest.mark.asyncio
async def test_manual_occurrence_creation_auto_creates_email_reminder(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    title = f"Manual reminder {uuid.uuid4()}"
    create_resp = await client.post(
        "/series",
        data={
            "title": title,
            "reminder_minutes_before": 45,
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
        f"/series/{series.id}/occurrences",
        data={"scheduled_at": "2030-01-10T09:00:00Z"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    manual_occ = (
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
    assert manual_occ is not None

    reminder = (
        (
            await db_session.execute(
                select(Reminder)
                .where(Reminder.occurrence_id == manual_occ.id)
                .order_by(Reminder.send_at.desc())
            )
        )
        .scalars()
        .first()
    )
    assert reminder is not None
    assert reminder.channel == ReminderChannel.email
    assert _as_utc(reminder.send_at) == _as_utc(manual_occ.scheduled_at) - timedelta(minutes=45)


@pytest.mark.asyncio
async def test_create_series_rejects_invalid_reminder_minutes_before(client: AsyncClient) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    resp = await client.post(
        "/series",
        data={
            "title": "Invalid Reminder",
            "reminder_minutes_before": -1,
            "recurrence_start_date": "2030-01-01",
            "recurrence_time": "09:00",
            "recurrence_timezone": "UTC",
            "recurrence_freq": "DAILY",
            "recurrence_interval": 1,
            "generate_count": 1,
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "reminder_minutes_before must be between 0 and 43200"


@pytest.mark.asyncio
async def test_create_series_rejects_invalid_generate_count(client: AsyncClient) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    resp = await client.post(
        "/series",
        data={
            "title": "Invalid Count",
            "reminder_minutes_before": 60,
            "recurrence_start_date": "2030-01-01",
            "recurrence_time": "09:00",
            "recurrence_timezone": "UTC",
            "recurrence_freq": "DAILY",
            "recurrence_interval": 1,
            "generate_count": 0,
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "generate_count must be between 1 and 200"


@pytest.mark.asyncio
async def test_create_series_rejects_invalid_recurrence_interval(client: AsyncClient) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    resp = await client.post(
        "/series",
        data={
            "title": "Invalid Interval",
            "reminder_minutes_before": 60,
            "recurrence_start_date": "2030-01-01",
            "recurrence_time": "09:00",
            "recurrence_timezone": "UTC",
            "recurrence_freq": "DAILY",
            "recurrence_interval": 0,
            "generate_count": 1,
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "recurrence_interval must be between 1 and 365"


@pytest.mark.asyncio
async def test_create_series_rejects_invalid_monthly_bymonthday(client: AsyncClient) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    resp = await client.post(
        "/series",
        data={
            "title": "Invalid Month Day",
            "reminder_minutes_before": 60,
            "recurrence_start_date": "2030-01-01",
            "recurrence_time": "09:00",
            "recurrence_timezone": "UTC",
            "recurrence_freq": "MONTHLY",
            "recurrence_interval": 1,
            "monthly_mode": "monthday",
            "monthly_bymonthday": "not-a-number",
            "generate_count": 1,
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid monthly day"


@pytest.mark.asyncio
async def test_create_series_rejects_invalid_recurrence_settings(client: AsyncClient) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    resp = await client.post(
        "/series",
        data={
            "title": "Invalid Rule",
            "reminder_minutes_before": 60,
            "recurrence_start_date": "2030-01-01",
            "recurrence_time": "09:00",
            "recurrence_timezone": "UTC",
            "recurrence_freq": "NOT_A_FREQ",
            "recurrence_interval": 1,
            "generate_count": 1,
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid recurrence settings"


@pytest.mark.asyncio
async def test_create_series_maps_service_value_error_to_bad_request(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    async def _raise_value_error(*args: object, **kwargs: object) -> tuple[object, list[object]]:
        raise ValueError("service rejected series")

    monkeypatch.setattr(series_routes, "create_series_with_occurrences", _raise_value_error)

    resp = await client.post(
        "/series",
        data={
            "title": "Service Error",
            "reminder_minutes_before": 60,
            "recurrence_start_date": "2030-01-01",
            "recurrence_time": "09:00",
            "recurrence_timezone": "UTC",
            "recurrence_freq": "DAILY",
            "recurrence_interval": 1,
            "generate_count": 1,
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "service rejected series"


@pytest.mark.asyncio
async def test_create_occurrence_404_when_series_not_owned(client: AsyncClient) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    resp = await client.post(
        f"/series/{uuid.uuid4()}/occurrences",
        data={"scheduled_at": "2030-01-10T09:00:00Z"},
        follow_redirects=False,
    )

    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_manual_occurrence_creation_skips_reminder_when_default_disabled(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENDABLE_ENABLE_DEFAULT_EMAIL_REMINDERS", "false")
    await login_user(client, "alice@example.com", "pw-alice")

    title = f"Manual no reminder {uuid.uuid4()}"
    create_resp = await client.post(
        "/series",
        data={
            "title": title,
            "reminder_minutes_before": 45,
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
        f"/series/{series.id}/occurrences",
        data={"scheduled_at": "2030-01-10T09:00:00Z"},
        follow_redirects=False,
    )
    assert resp.status_code == 303

    manual_occ = (
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
    assert manual_occ is not None

    reminder = (
        (
            await db_session.execute(
                select(Reminder)
                .where(Reminder.occurrence_id == manual_occ.id)
                .order_by(Reminder.send_at.desc())
            )
        )
        .scalars()
        .first()
    )
    assert reminder is None


@pytest.mark.asyncio
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

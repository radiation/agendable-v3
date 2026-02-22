from __future__ import annotations

from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import MeetingSeries, User


async def login_user(
    client: AsyncClient,
    email: str,
    password: str,
    *,
    first_name: str = "Test",
    last_name: str = "User",
) -> None:
    resp = await client.post(
        "/signup",
        data={
            "first_name": first_name,
            "last_name": last_name,
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


async def create_series(
    client: AsyncClient,
    db_session: AsyncSession,
    *,
    owner_email: str,
    title: str,
    reminder_minutes_before: int = 60,
) -> MeetingSeries:
    resp = await client.post(
        "/series",
        data={
            "title": title,
            "reminder_minutes_before": reminder_minutes_before,
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

    owner = (await db_session.execute(select(User).where(User.email == owner_email))).scalar_one()
    return (
        await db_session.execute(
            select(MeetingSeries).where(
                MeetingSeries.owner_user_id == owner.id,
                MeetingSeries.title == title,
            )
        )
    ).scalar_one()

from __future__ import annotations

import uuid

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.models import MeetingSeries, User


async def _login(client: AsyncClient, email: str, password: str) -> None:
    resp = await client.post(
        "/login",
        data={"email": email, "password": password},
        follow_redirects=True,
    )
    assert resp.status_code == 200


@pytest.mark.asyncio
async def test_create_series_requires_auth(client: AsyncClient) -> None:
    resp = await client.post("/series", data={"title": "X", "default_interval_days": 7})
    assert resp.status_code == 401


@pytest.mark.asyncio
async def test_create_series_and_list_is_scoped_to_user(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    title = f"1:1 {uuid.uuid4()}"

    await _login(client, "alice@example.com", "pw-alice")

    resp = await client.post(
        "/series",
        data={"title": title, "default_interval_days": 7},
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
    await _login(client, "bob@example.com", "pw-bob")

    resp = await client.get("/")
    assert resp.status_code == 200
    assert title not in resp.text

    # Bob also should not be able to view Alice's series detail.
    resp = await client.get(f"/series/{series.id}")
    assert resp.status_code == 404

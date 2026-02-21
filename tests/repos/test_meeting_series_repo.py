from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import agendable.db as db
from agendable.db.models import MeetingSeries, User
from agendable.db.repos.meeting_series import MeetingSeriesRepository
from agendable.db.repos.users import UserRepository


@pytest.mark.asyncio
async def test_meeting_series_repo_owner_scoping(db_session: AsyncSession) -> None:
    users = UserRepository(db_session)

    alice = User(
        email=f"alice-{uuid.uuid4()}@example.com",
        first_name="Alice",
        last_name="Example",
        display_name="Alice Example",
        timezone="UTC",
        password_hash=None,
    )
    bob = User(
        email=f"bob-{uuid.uuid4()}@example.com",
        first_name="Bob",
        last_name="Example",
        display_name="Bob Example",
        timezone="UTC",
        password_hash=None,
    )

    await users.add(alice)
    await users.add(bob)
    await users.commit()

    series_repo = MeetingSeriesRepository(db_session)
    series = MeetingSeries(owner_user_id=alice.id, title="1:1", default_interval_days=7)
    await series_repo.add(series)
    await series_repo.commit()

    # Can fetch as owner
    got = await series_repo.get_for_owner(series.id, alice.id)
    assert got is not None
    assert got.id == series.id

    # Cannot fetch as other user
    got = await series_repo.get_for_owner(series.id, bob.id)
    assert got is None

    # list_for_owner returns the series for Alice, not Bob
    alice_list = await series_repo.list_for_owner(alice.id)
    assert any(s.id == series.id for s in alice_list)

    bob_list = await series_repo.list_for_owner(bob.id)
    assert all(s.id != series.id for s in bob_list)

    # Verify the same behavior holds in a fresh session (no identity-map artifacts).
    async with db.SessionMaker() as verify_session:
        verify_repo = MeetingSeriesRepository(verify_session)
        assert await verify_repo.get_for_owner(series.id, bob.id) is None

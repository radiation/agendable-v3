from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

import agendable.db as db
from agendable.models import User
from agendable.repos.users import UserRepository


async def _new_user(email: str) -> User:
    return User(email=email.strip().lower(), display_name=email.strip().lower(), password_hash=None)


@pytest.mark.asyncio
async def test_user_repo_add_get_commit(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    user = await _new_user(f"u-{uuid.uuid4()}@example.com")

    await repo.add(user)
    await repo.commit()

    async with db.SessionMaker() as verify_session:
        verify_repo = UserRepository(verify_session)
        got = await verify_repo.get_by_id(user.id)
        assert got is not None
        assert got.email == user.email


@pytest.mark.asyncio
async def test_user_repo_first_one_where(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    email = f"u-{uuid.uuid4()}@example.com"
    user = await _new_user(email)
    await repo.add(user)
    await repo.commit()

    got_first = await repo.first_where(User.email == email)
    assert got_first is not None
    assert got_first.id == user.id

    got_one = await repo.one_where(User.email == email)
    assert got_one.id == user.id

    got_missing = await repo.first_where(User.email == "missing@example.com")
    assert got_missing is None


@pytest.mark.asyncio
async def test_user_repo_list_offset_limit(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)

    emails = [
        f"a-{uuid.uuid4()}@example.com",
        f"b-{uuid.uuid4()}@example.com",
        f"c-{uuid.uuid4()}@example.com",
    ]

    for email in emails:
        await repo.add(await _new_user(email))

    await repo.commit()

    all_users = await repo.list(limit=100)
    all_emails = {u.email for u in all_users}
    assert set(emails).issubset(all_emails)

    limited = await repo.list(limit=2)
    assert len(limited) == 2

    offset = await repo.list(offset=1, limit=2)
    assert len(offset) == 2


@pytest.mark.asyncio
async def test_user_repo_patch_and_delete(db_session: AsyncSession) -> None:
    repo = UserRepository(db_session)
    user = await _new_user(f"u-{uuid.uuid4()}@example.com")

    await repo.add(user)
    await repo.commit()

    await repo.patch(user, {"display_name": "New Name"})
    await repo.commit()

    async with db.SessionMaker() as verify_session:
        verify_repo = UserRepository(verify_session)
        got = await verify_repo.get_by_id(user.id)
        assert got is not None
        assert got.display_name == "New Name"

    await repo.delete(user)
    await repo.commit()

    async with db.SessionMaker() as verify_session:
        verify_repo = UserRepository(verify_session)
        got = await verify_repo.get_by_id(user.id)
        assert got is None

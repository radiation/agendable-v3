from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import User, UserRole
from tests.auth.admin_test_helpers import promote_signed_in_user_to_admin


@pytest.mark.asyncio
async def test_admin_users_requires_admin_role(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    resp = await client.post(
        "/signup",
        data={
            "first_name": "Alice",
            "last_name": "Example",
            "timezone": "UTC",
            "email": "alice-admin-test@example.com",
            "password": "pw123456",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    denied = await client.get("/admin/users")
    assert denied.status_code == 403

    alice = (
        await db_session.execute(select(User).where(User.email == "alice-admin-test@example.com"))
    ).scalar_one()
    alice.role = UserRole.admin
    await db_session.commit()

    allowed = await client.get("/admin/users")
    assert allowed.status_code == 200
    assert "Users" in allowed.text
    assert "alice-admin-test@example.com" in allowed.text


@pytest.mark.asyncio
async def test_bootstrap_admin_email_auto_promotes_user(
    client: AsyncClient, db_session: AsyncSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AGENDABLE_BOOTSTRAP_ADMIN_EMAIL", "bootstrap-admin@example.com")

    resp = await client.post(
        "/signup",
        data={
            "first_name": "Bootstrap",
            "last_name": "Admin",
            "timezone": "UTC",
            "email": "bootstrap-admin@example.com",
            "password": "pw123456",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200

    user = (
        await db_session.execute(select(User).where(User.email == "bootstrap-admin@example.com"))
    ).scalar_one()
    assert user.role == UserRole.admin

    admin_page = await client.get("/admin/users")
    assert admin_page.status_code == 200


@pytest.mark.asyncio
async def test_inactive_admin_cannot_access_admin_users(
    client: AsyncClient, db_session: AsyncSession
) -> None:
    signup = await client.post(
        "/signup",
        data={
            "first_name": "Inactive",
            "last_name": "Admin",
            "timezone": "UTC",
            "email": "inactive-admin@example.com",
            "password": "pw123456",
        },
        follow_redirects=True,
    )
    assert signup.status_code == 200

    admin_user = await promote_signed_in_user_to_admin(
        db_session, email="inactive-admin@example.com"
    )
    admin_user.is_active = False
    await db_session.commit()

    admin_page = await client.get("/admin/users", follow_redirects=False)
    assert admin_page.status_code == 401

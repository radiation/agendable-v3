from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import User, UserRole


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

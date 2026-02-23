from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.auth import hash_password
from agendable.db.models import ExternalIdentity, User, UserRole
from agendable.web.routes import auth as auth_routes
from tests.auth.account_linking_test_helpers import (
    enable_oidc_env,
    get_user_by_email,
    signup_and_login,
)


@pytest.mark.asyncio
async def test_profile_shows_linked_identity(client: AsyncClient, db_session: AsyncSession) -> None:
    await signup_and_login(
        client,
        first_name="Link",
        last_name="Viewer",
        email="link-viewer@example.com",
    )
    user = await get_user_by_email(db_session, "link-viewer@example.com")

    identity = ExternalIdentity(
        user_id=user.id,
        provider="oidc",
        subject="sub-link-viewer",
        email=user.email,
    )
    db_session.add(identity)
    await db_session.commit()

    profile = await client.get("/profile")
    assert profile.status_code == 200
    assert "Linked sign-in methods" in profile.text
    assert "oidc" in profile.text
    assert f"/profile/identities/{identity.id}/unlink" in profile.text


@pytest.mark.asyncio
async def test_profile_unlink_removes_identity_for_password_user(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await signup_and_login(
        client,
        first_name="Unlink",
        last_name="Success",
        email="unlink-success@example.com",
    )
    user = await get_user_by_email(db_session, "unlink-success@example.com")

    identity = ExternalIdentity(
        user_id=user.id,
        provider="oidc",
        subject="sub-unlink-success",
        email=user.email,
    )
    db_session.add(identity)
    await db_session.commit()

    unlink = await client.post(
        f"/profile/identities/{identity.id}/unlink",
        follow_redirects=False,
    )
    assert unlink.status_code == 303
    assert unlink.headers["location"] == "/profile"

    deleted = (
        await db_session.execute(
            select(ExternalIdentity)
            .where(ExternalIdentity.id == identity.id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    assert deleted is None


@pytest.mark.asyncio
async def test_profile_unlink_blocks_only_signin_method(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await signup_and_login(
        client,
        first_name="Unlink",
        last_name="Blocked",
        email="unlink-blocked@example.com",
    )
    user = await get_user_by_email(db_session, "unlink-blocked@example.com")
    user.password_hash = None

    identity = ExternalIdentity(
        user_id=user.id,
        provider="oidc",
        subject="sub-unlink-blocked",
        email=user.email,
    )
    db_session.add(identity)
    await db_session.commit()

    unlink = await client.post(
        f"/profile/identities/{identity.id}/unlink",
        follow_redirects=False,
    )
    assert unlink.status_code == 400
    assert "You cannot unlink your only sign-in method." in unlink.text

    still_present = await db_session.get(ExternalIdentity, identity.id)
    assert still_present is not None


@pytest.mark.asyncio
async def test_profile_unlink_rejects_other_users_identity(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await signup_and_login(
        client,
        first_name="Own",
        last_name="User",
        email="own-user@example.com",
    )

    other_user = User(
        email="other-user@example.com",
        first_name="Other",
        last_name="User",
        display_name="Other User",
        timezone="UTC",
        role=UserRole.user,
        password_hash=hash_password("pw123456"),
    )
    db_session.add(other_user)
    await db_session.flush()

    other_identity = ExternalIdentity(
        user_id=other_user.id,
        provider="oidc",
        subject="sub-other-user",
        email=other_user.email,
    )
    db_session.add(other_identity)
    await db_session.commit()

    unlink = await client.post(
        f"/profile/identities/{other_identity.id}/unlink",
        follow_redirects=False,
    )
    assert unlink.status_code == 404


@pytest.mark.asyncio
async def test_admin_users_page_shows_linked_identity_summary(
    client: AsyncClient,
    db_session: AsyncSession,
) -> None:
    await signup_and_login(
        client,
        first_name="Admin",
        last_name="Summary",
        email="admin-summary@example.com",
    )
    admin_user = await get_user_by_email(db_session, "admin-summary@example.com")
    admin_user.role = UserRole.admin

    managed_user = User(
        email="managed-summary@example.com",
        first_name="Managed",
        last_name="Summary",
        display_name="Managed Summary",
        timezone="UTC",
        role=UserRole.user,
        password_hash=hash_password("pw123456"),
    )
    db_session.add(managed_user)
    await db_session.flush()

    identity = ExternalIdentity(
        user_id=managed_user.id,
        provider="oidc",
        subject="sub-managed-summary",
        email=managed_user.email,
    )
    db_session.add(identity)
    await db_session.commit()

    page = await client.get("/admin/users")
    assert page.status_code == 200
    assert "Linked SSO" in page.text
    assert "1 linked (oidc)" in page.text


@pytest.mark.asyncio
async def test_profile_link_start_requires_current_password(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_oidc_env(monkeypatch)

    await signup_and_login(
        client,
        first_name="Link",
        last_name="Password",
        email="link-password-required@example.com",
    )
    _ = await get_user_by_email(db_session, "link-password-required@example.com")

    response = await client.post(
        "/profile/identities/link/start",
        data={"password": "wrong-password"},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert "Enter your current password to link an SSO account." in response.text


@pytest.mark.asyncio
async def test_profile_link_start_returns_404_when_oidc_disabled(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(auth_routes, "oidc_enabled", lambda: False)

    await signup_and_login(
        client,
        first_name="Link",
        last_name="Disabled",
        email="link-disabled@example.com",
    )
    _ = await get_user_by_email(db_session, "link-disabled@example.com")

    response = await client.post(
        "/profile/identities/link/start",
        data={"password": "anything"},
        follow_redirects=False,
    )
    assert response.status_code == 404

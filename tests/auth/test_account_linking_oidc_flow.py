from __future__ import annotations

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.auth import hash_password
from agendable.db.models import ExternalIdentity, User, UserRole
from agendable.web.routes import auth as auth_routes
from tests.auth.account_linking_test_helpers import (
    FakeOidcLinkClient,
    enable_oidc_env,
    get_user_by_email,
    signup_and_login,
)


@pytest.mark.asyncio
async def test_profile_link_flow_links_identity_after_oidc_callback(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_oidc_env(monkeypatch)

    await signup_and_login(
        client,
        first_name="Link",
        last_name="Success",
        email="link-flow-success@example.com",
    )

    fake_client = FakeOidcLinkClient(
        {
            "sub": "sub-link-flow-success",
            "email": "link-flow-success@example.com",
            "email_verified": True,
        }
    )
    monkeypatch.setattr(auth_routes, "_oidc_oauth_client", lambda: fake_client)

    start = await client.post(
        "/profile/identities/link/start",
        data={"password": "pw123456"},
        follow_redirects=False,
    )
    assert start.status_code == 303
    assert start.headers["location"] == "/auth/oidc/start"

    oidc_start = await client.get("/auth/oidc/start", follow_redirects=False)
    assert oidc_start.status_code == 302

    callback = await client.get("/auth/oidc/callback", follow_redirects=False)
    assert callback.status_code == 303
    assert callback.headers["location"] == "/profile"

    user = await get_user_by_email(db_session, "link-flow-success@example.com")
    linked = (
        await db_session.execute(
            select(ExternalIdentity).where(
                ExternalIdentity.user_id == user.id,
                ExternalIdentity.subject == "sub-link-flow-success",
            )
        )
    ).scalar_one()
    assert linked.provider == "oidc"


@pytest.mark.asyncio
async def test_profile_link_callback_rejects_email_mismatch(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_oidc_env(monkeypatch)

    await signup_and_login(
        client,
        first_name="Link",
        last_name="Mismatch",
        email="link-flow-mismatch@example.com",
    )

    fake_client = FakeOidcLinkClient(
        {
            "sub": "sub-link-flow-mismatch",
            "email": "different-email@example.com",
            "email_verified": True,
        }
    )
    monkeypatch.setattr(auth_routes, "_oidc_oauth_client", lambda: fake_client)

    start = await client.post(
        "/profile/identities/link/start",
        data={"password": "pw123456"},
        follow_redirects=False,
    )
    assert start.status_code == 303

    callback = await client.get("/auth/oidc/callback", follow_redirects=False)
    assert callback.status_code == 403
    assert "SSO account email must match your profile email." in callback.text

    user = await get_user_by_email(db_session, "link-flow-mismatch@example.com")
    linked = (
        (
            await db_session.execute(
                select(ExternalIdentity).where(ExternalIdentity.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    assert linked == []


@pytest.mark.asyncio
async def test_profile_link_callback_rejects_identity_linked_to_other_user(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_oidc_env(monkeypatch)

    await signup_and_login(
        client,
        first_name="Link",
        last_name="Owner",
        email="link-owner@example.com",
    )
    owner = await get_user_by_email(db_session, "link-owner@example.com")

    other_user = User(
        email="other-link-owner@example.com",
        first_name="Other",
        last_name="LinkOwner",
        display_name="Other LinkOwner",
        timezone="UTC",
        role=UserRole.user,
        password_hash=hash_password("pw123456"),
    )
    db_session.add(other_user)
    await db_session.flush()

    db_session.add(
        ExternalIdentity(
            user_id=other_user.id,
            provider="oidc",
            subject="sub-already-linked",
            email=other_user.email,
        )
    )
    await db_session.commit()

    fake_client = FakeOidcLinkClient(
        {
            "sub": "sub-already-linked",
            "email": owner.email,
            "email_verified": True,
        }
    )
    monkeypatch.setattr(auth_routes, "_oidc_oauth_client", lambda: fake_client)

    start = await client.post(
        "/profile/identities/link/start",
        data={"password": "pw123456"},
        follow_redirects=False,
    )
    assert start.status_code == 303

    callback = await client.get("/auth/oidc/callback", follow_redirects=False)
    assert callback.status_code == 403
    assert "This SSO account is already linked to a different user." in callback.text

    owner_links = (
        (
            await db_session.execute(
                select(ExternalIdentity).where(ExternalIdentity.user_id == owner.id)
            )
        )
        .scalars()
        .all()
    )
    assert owner_links == []


@pytest.mark.asyncio
async def test_profile_link_callback_handles_deleted_linking_user(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enable_oidc_env(monkeypatch)

    await signup_and_login(
        client,
        first_name="Link",
        last_name="Deleted",
        email="link-deleted@example.com",
    )
    user = await get_user_by_email(db_session, "link-deleted@example.com")

    fake_client = FakeOidcLinkClient(
        {
            "sub": "sub-link-deleted",
            "email": "link-deleted@example.com",
            "email_verified": True,
        }
    )
    monkeypatch.setattr(auth_routes, "_oidc_oauth_client", lambda: fake_client)

    start = await client.post(
        "/profile/identities/link/start",
        data={"password": "pw123456"},
        follow_redirects=False,
    )
    assert start.status_code == 303

    await db_session.delete(user)
    await db_session.commit()

    callback = await client.get("/auth/oidc/callback", follow_redirects=False)
    assert callback.status_code == 303
    assert callback.headers["location"] == "/login"

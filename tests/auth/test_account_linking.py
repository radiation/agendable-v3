from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.responses import RedirectResponse
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.auth import hash_password
from agendable.db.models import ExternalIdentity, User, UserRole
from agendable.web.routes import auth as auth_routes


@dataclass
class _FakeOidcLinkClient:
    userinfo_payload: dict[str, object]

    async def authorize_redirect(
        self,
        request: object,
        redirect_uri: str,
        **kwargs: object,
    ) -> RedirectResponse:
        return RedirectResponse(url="https://idp.example.test/authorize", status_code=302)

    async def authorize_access_token(self, request: object) -> dict[str, str]:
        return {"access_token": "test-token", "id_token": "id-token"}

    async def parse_id_token(self, request: object, token: object) -> dict[str, object]:
        return self.userinfo_payload

    async def userinfo(self, token: object) -> dict[str, object]:
        return self.userinfo_payload


async def _signup_and_login(
    client: AsyncClient,
    *,
    first_name: str,
    last_name: str,
    email: str,
    password: str = "pw123456",
) -> None:
    response = await client.post(
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
    assert response.status_code == 200


async def _get_user_by_email(db_session: AsyncSession, email: str) -> User:
    return (await db_session.execute(select(User).where(User.email == email))).scalar_one()


@pytest.mark.asyncio
async def test_profile_shows_linked_identity(client: AsyncClient, db_session: AsyncSession) -> None:
    await _signup_and_login(
        client,
        first_name="Link",
        last_name="Viewer",
        email="link-viewer@example.com",
    )
    user = await _get_user_by_email(db_session, "link-viewer@example.com")

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
    await _signup_and_login(
        client,
        first_name="Unlink",
        last_name="Success",
        email="unlink-success@example.com",
    )
    user = await _get_user_by_email(db_session, "unlink-success@example.com")

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
    await _signup_and_login(
        client,
        first_name="Unlink",
        last_name="Blocked",
        email="unlink-blocked@example.com",
    )
    user = await _get_user_by_email(db_session, "unlink-blocked@example.com")
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
    await _signup_and_login(
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
    await _signup_and_login(
        client,
        first_name="Admin",
        last_name="Summary",
        email="admin-summary@example.com",
    )
    admin_user = await _get_user_by_email(db_session, "admin-summary@example.com")
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
) -> None:
    await _signup_and_login(
        client,
        first_name="Link",
        last_name="Password",
        email="link-password-required@example.com",
    )
    _ = await _get_user_by_email(db_session, "link-password-required@example.com")

    response = await client.post(
        "/profile/identities/link/start",
        data={"password": "wrong-password"},
        follow_redirects=False,
    )
    assert response.status_code == 401
    assert "Enter your current password to link an SSO account." in response.text


@pytest.mark.asyncio
async def test_profile_link_flow_links_identity_after_oidc_callback(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENDABLE_OIDC_CLIENT_ID", "test-client")
    monkeypatch.setenv("AGENDABLE_OIDC_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv(
        "AGENDABLE_OIDC_METADATA_URL", "https://example.com/.well-known/openid-configuration"
    )

    await _signup_and_login(
        client,
        first_name="Link",
        last_name="Success",
        email="link-flow-success@example.com",
    )

    fake_client = _FakeOidcLinkClient(
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

    user = await _get_user_by_email(db_session, "link-flow-success@example.com")
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
    monkeypatch.setenv("AGENDABLE_OIDC_CLIENT_ID", "test-client")
    monkeypatch.setenv("AGENDABLE_OIDC_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv(
        "AGENDABLE_OIDC_METADATA_URL", "https://example.com/.well-known/openid-configuration"
    )

    await _signup_and_login(
        client,
        first_name="Link",
        last_name="Mismatch",
        email="link-flow-mismatch@example.com",
    )

    fake_client = _FakeOidcLinkClient(
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

    user = await _get_user_by_email(db_session, "link-flow-mismatch@example.com")
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
    monkeypatch.setenv("AGENDABLE_OIDC_CLIENT_ID", "test-client")
    monkeypatch.setenv("AGENDABLE_OIDC_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv(
        "AGENDABLE_OIDC_METADATA_URL", "https://example.com/.well-known/openid-configuration"
    )

    await _signup_and_login(
        client,
        first_name="Link",
        last_name="Owner",
        email="link-owner@example.com",
    )
    owner = await _get_user_by_email(db_session, "link-owner@example.com")

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

    fake_client = _FakeOidcLinkClient(
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

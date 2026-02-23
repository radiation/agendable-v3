from __future__ import annotations

from dataclasses import dataclass

import pytest
from httpx import AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import ExternalIdentity, User
from agendable.web.routes import auth as auth_routes


@dataclass
class _FakeGoogleClient:
    userinfo_payload: dict[str, object]

    async def authorize_access_token(self, request: object) -> dict[str, str]:
        return {"access_token": "test-token"}

    async def parse_id_token(self, request: object, token: object) -> dict[str, object]:
        return self.userinfo_payload

    async def userinfo(self, token: object) -> dict[str, object]:
        return self.userinfo_payload


@pytest.mark.asyncio
async def test_oidc_callback_autoprovisions_user_and_links_identity(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENDABLE_GOOGLE_CLIENT_ID", "test-client")
    monkeypatch.setenv("AGENDABLE_GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(
        auth_routes,
        "_google_oauth_client",
        lambda: _FakeGoogleClient(
            {
                "sub": "sub-123",
                "email": "alice@example.com",
                "email_verified": True,
                "given_name": "Alice",
                "family_name": "Example",
            }
        ),
    )

    response = await client.get("/auth/google/callback", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"

    user = (
        await db_session.execute(select(User).where(User.email == "alice@example.com"))
    ).scalar_one()
    assert user.first_name == "Alice"
    assert user.last_name == "Example"
    assert user.password_hash is None

    identity = (
        await db_session.execute(
            select(ExternalIdentity).where(
                ExternalIdentity.provider == "google",
                ExternalIdentity.subject == "sub-123",
            )
        )
    ).scalar_one()
    assert identity.user_id == user.id


@pytest.mark.asyncio
async def test_oidc_callback_links_existing_user_without_creating_duplicate(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENDABLE_GOOGLE_CLIENT_ID", "test-client")
    monkeypatch.setenv("AGENDABLE_GOOGLE_CLIENT_SECRET", "test-secret")

    signup = await client.post(
        "/signup",
        data={
            "first_name": "Bob",
            "last_name": "Example",
            "timezone": "UTC",
            "email": "bob@example.com",
            "password": "pw-bob",
        },
        follow_redirects=True,
    )
    assert signup.status_code == 200
    await client.post("/logout", follow_redirects=True)

    monkeypatch.setattr(
        auth_routes,
        "_google_oauth_client",
        lambda: _FakeGoogleClient(
            {
                "sub": "sub-bob",
                "email": "bob@example.com",
                "email_verified": True,
                "given_name": "Bob",
                "family_name": "Example",
            }
        ),
    )

    response = await client.get("/auth/google/callback", follow_redirects=False)
    assert response.status_code == 303
    assert response.headers["location"] == "/dashboard"

    user_count = (await db_session.execute(select(func.count(User.id)))).scalar_one()
    assert user_count == 1

    identity = (
        await db_session.execute(
            select(ExternalIdentity).where(
                ExternalIdentity.provider == "google",
                ExternalIdentity.subject == "sub-bob",
            )
        )
    ).scalar_one()
    bob = (
        await db_session.execute(select(User).where(User.email == "bob@example.com"))
    ).scalar_one()
    assert identity.user_id == bob.id


@pytest.mark.asyncio
async def test_oidc_autoprovision_name_fallback_uses_email_localpart(
    client: AsyncClient,
    db_session: AsyncSession,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENDABLE_GOOGLE_CLIENT_ID", "test-client")
    monkeypatch.setenv("AGENDABLE_GOOGLE_CLIENT_SECRET", "test-secret")
    monkeypatch.setattr(
        auth_routes,
        "_google_oauth_client",
        lambda: _FakeGoogleClient(
            {
                "sub": "sub-charlie",
                "email": "charlie@example.com",
                "email_verified": True,
            }
        ),
    )

    response = await client.get("/auth/google/callback", follow_redirects=False)
    assert response.status_code == 303

    user = (
        await db_session.execute(select(User).where(User.email == "charlie@example.com"))
    ).scalar_one()
    assert user.first_name == "charlie"
    assert user.last_name == ""
    assert user.display_name == "charlie"

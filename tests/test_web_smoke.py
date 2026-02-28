from __future__ import annotations

import pytest
from httpx import AsyncClient

from agendable.web.routes import auth as auth_routes


@pytest.mark.asyncio
async def test_index_anonymous_redirects_to_login(client: AsyncClient) -> None:
    resp = await client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_login_page_hides_oidc_when_disabled(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth_routes, "oidc_enabled", lambda: False)

    resp = await client.get("/login")
    assert resp.status_code == 200
    assert "Sign in with SSO" not in resp.text


@pytest.mark.asyncio
async def test_profile_page_hides_oidc_link_when_disabled(
    client: AsyncClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(auth_routes, "oidc_enabled", lambda: False)

    await client.post(
        "/signup",
        data={
            "first_name": "No",
            "last_name": "SSO",
            "timezone": "UTC",
            "email": "no-sso-profile@example.com",
            "password": "pw123456",
        },
        follow_redirects=True,
    )

    resp = await client.get("/profile")
    assert resp.status_code == 200
    assert "Link SSO account" not in resp.text


@pytest.mark.asyncio
async def test_signup_creates_user_and_sets_session(client: AsyncClient) -> None:
    resp = await client.post(
        "/signup",
        data={
            "first_name": "Alice",
            "last_name": "Example",
            "timezone": "UTC",
            "email": "alice@example.com",
            "password": "pw123456",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Signed in as Alice Example" in resp.text
    assert "Dashboard" in resp.text


@pytest.mark.asyncio
async def test_password_login_rejects_unknown_user(client: AsyncClient) -> None:
    resp = await client.post(
        "/login",
        data={"email": "unknown@example.com", "password": "pw123"},
    )
    assert resp.status_code == 401
    assert "Account not found" in resp.text


@pytest.mark.asyncio
async def test_password_login_rejects_wrong_password(client: AsyncClient) -> None:
    await client.post(
        "/signup",
        data={
            "first_name": "Bob",
            "last_name": "Example",
            "timezone": "UTC",
            "email": "bob@example.com",
            "password": "pw-right",
        },
        follow_redirects=True,
    )

    resp = await client.post(
        "/login",
        data={"email": "bob@example.com", "password": "pw-wrong"},
    )
    assert resp.status_code == 401
    assert "Invalid email or password" in resp.text


@pytest.mark.asyncio
async def test_signup_rejects_invalid_timezone(client: AsyncClient) -> None:
    resp = await client.post(
        "/signup",
        data={
            "first_name": "Alice",
            "last_name": "Example",
            "timezone": "Not/A_Real_Timezone",
            "email": "alice-timezone@example.com",
            "password": "pw123456",
        },
    )

    assert resp.status_code == 400
    assert "Unknown timezone" in resp.text


@pytest.mark.asyncio
async def test_request_id_header_is_propagated_or_generated(client: AsyncClient) -> None:
    with_header = await client.get(
        "/", headers={"X-Request-ID": "req-test-123"}, follow_redirects=False
    )
    assert with_header.status_code == 303
    assert with_header.headers["x-request-id"] == "req-test-123"

    generated = await client.get("/", follow_redirects=False)
    assert generated.status_code == 303
    assert generated.headers.get("x-request-id")

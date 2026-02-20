from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_index_anonymous_renders(client: AsyncClient) -> None:
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "Sign in to create and view meeting series" in resp.text


@pytest.mark.asyncio
async def test_login_page_hides_google_when_disabled(client: AsyncClient) -> None:
    resp = await client.get("/login")
    assert resp.status_code == 200
    assert "Sign in with Google" not in resp.text


@pytest.mark.asyncio
async def test_password_login_provisions_user_and_sets_session(client: AsyncClient) -> None:
    resp = await client.post(
        "/login",
        data={"email": "alice@example.com", "password": "pw123456"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Signed in as alice@example.com" in resp.text
    assert "Meeting series" in resp.text


@pytest.mark.asyncio
async def test_password_login_rejects_wrong_password(client: AsyncClient) -> None:
    await client.post(
        "/login",
        data={"email": "bob@example.com", "password": "pw-right"},
        follow_redirects=True,
    )

    resp = await client.post(
        "/login",
        data={"email": "bob@example.com", "password": "pw-wrong"},
    )
    assert resp.status_code == 401
    assert "Invalid email or password" in resp.text

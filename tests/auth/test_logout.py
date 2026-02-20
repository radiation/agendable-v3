from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_logout_clears_session(client: AsyncClient) -> None:
    # Login
    resp = await client.post(
        "/login",
        data={"email": "alice@example.com", "password": "pw-alice"},
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Signed in as alice@example.com" in resp.text

    # Logout
    resp = await client.post("/logout", follow_redirects=True)
    assert resp.status_code == 200

    # Confirm we're anonymous again
    resp = await client.get("/")
    assert resp.status_code == 200
    assert "Signed in as" not in resp.text
    assert "Sign in to create and view meeting series" in resp.text

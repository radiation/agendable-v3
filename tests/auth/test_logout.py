from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_logout_clears_session(client: AsyncClient) -> None:
    # Create + sign in
    resp = await client.post(
        "/signup",
        data={
            "first_name": "Alice",
            "last_name": "Example",
            "timezone": "UTC",
            "email": "alice@example.com",
            "password": "pw-alice",
        },
        follow_redirects=True,
    )
    assert resp.status_code == 200
    assert "Signed in as Alice Example" in resp.text

    # Logout
    resp = await client.post("/logout", follow_redirects=True)
    assert resp.status_code == 200

    # Confirm we're anonymous again
    resp = await client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"

    resp = await client.get("/login")
    assert resp.status_code == 200
    assert "Signed in as" not in resp.text

from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_existing_user_wrong_password_stays_401(client: AsyncClient) -> None:
    # First login provisions the user.
    resp = await client.post(
        "/login",
        data={"email": "bob@example.com", "password": "pw-right"},
        follow_redirects=True,
    )
    assert resp.status_code == 200

    # Logout so we don't just reuse the existing session.
    resp = await client.post("/logout", follow_redirects=True)
    assert resp.status_code == 200

    # Wrong password should be rejected.
    resp = await client.post(
        "/login",
        data={"email": "bob@example.com", "password": "pw-wrong"},
    )
    assert resp.status_code == 401
    assert "Invalid email or password" in resp.text

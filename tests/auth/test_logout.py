from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_logout_clears_session(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="agendable.security.audit")

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

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "audit_event=auth.logout" in message
        and "outcome=success" in message
        and "actor_user_id=" in message
        for message in messages
    )

    # Confirm we're anonymous again
    resp = await client.get("/", follow_redirects=False)
    assert resp.status_code == 303
    assert resp.headers["location"] == "/login"

    resp = await client.get("/login")
    assert resp.status_code == 200
    assert "Signed in as" not in resp.text

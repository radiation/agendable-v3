from __future__ import annotations

import logging

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_existing_user_wrong_password_stays_401(client: AsyncClient) -> None:
    # Create the user.
    resp = await client.post(
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


@pytest.mark.asyncio
async def test_login_rate_limit_blocks_after_configured_ip_attempts(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENDABLE_LOGIN_RATE_LIMIT_IP_ATTEMPTS", "2")
    monkeypatch.setenv("AGENDABLE_LOGIN_RATE_LIMIT_ACCOUNT_ATTEMPTS", "99")

    signup = await client.post(
        "/signup",
        data={
            "first_name": "Rate",
            "last_name": "IP",
            "timezone": "UTC",
            "email": "rate-ip@example.com",
            "password": "pw-right",
        },
        follow_redirects=True,
    )
    assert signup.status_code == 200
    await client.post("/logout", follow_redirects=True)

    first = await client.post(
        "/login",
        data={"email": "rate-ip@example.com", "password": "wrong-1"},
    )
    second = await client.post(
        "/login",
        data={"email": "rate-ip@example.com", "password": "wrong-2"},
    )
    third = await client.post(
        "/login",
        data={"email": "rate-ip@example.com", "password": "wrong-3"},
    )

    assert first.status_code == 401
    assert second.status_code == 401
    assert third.status_code == 429
    assert "Too many login attempts" in third.text


@pytest.mark.asyncio
async def test_login_rate_limit_blocks_by_account_across_ips(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENDABLE_LOGIN_RATE_LIMIT_IP_ATTEMPTS", "99")
    monkeypatch.setenv("AGENDABLE_LOGIN_RATE_LIMIT_ACCOUNT_ATTEMPTS", "1")

    signup = await client.post(
        "/signup",
        data={
            "first_name": "Rate",
            "last_name": "Account",
            "timezone": "UTC",
            "email": "rate-account@example.com",
            "password": "pw-right",
        },
        follow_redirects=True,
    )
    assert signup.status_code == 200
    await client.post("/logout", follow_redirects=True)

    first = await client.post(
        "/login",
        data={"email": "rate-account@example.com", "password": "wrong-1"},
        headers={"x-forwarded-for": "10.0.0.1"},
    )
    second = await client.post(
        "/login",
        data={"email": "rate-account@example.com", "password": "wrong-2"},
        headers={"x-forwarded-for": "10.0.0.2"},
    )

    assert first.status_code == 401
    assert second.status_code == 429
    assert "Too many login attempts" in second.text


@pytest.mark.asyncio
async def test_password_login_emits_audit_events(
    client: AsyncClient,
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="agendable.security.audit")

    signup = await client.post(
        "/signup",
        data={
            "first_name": "Audit",
            "last_name": "Login",
            "timezone": "UTC",
            "email": "audit-login@example.com",
            "password": "pw-right",
        },
        follow_redirects=True,
    )
    assert signup.status_code == 200
    await client.post("/logout", follow_redirects=True)

    failed = await client.post(
        "/login",
        data={"email": "audit-login@example.com", "password": "pw-wrong"},
    )
    assert failed.status_code == 401

    success = await client.post(
        "/login",
        data={"email": "audit-login@example.com", "password": "pw-right"},
        follow_redirects=False,
    )
    assert success.status_code == 303

    messages = [record.getMessage() for record in caplog.records]
    assert any(
        "audit_event=auth.password_login" in message
        and "outcome=denied" in message
        and "reason=invalid_credentials" in message
        for message in messages
    )
    assert any(
        "audit_event=auth.password_login" in message and "outcome=success" in message
        for message in messages
    )

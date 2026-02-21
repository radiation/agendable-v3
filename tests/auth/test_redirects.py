from __future__ import annotations

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_signup_success_redirects_to_dashboard(client: AsyncClient) -> None:
    resp = await client.post(
        "/signup",
        data={
            "first_name": "Alice",
            "last_name": "Example",
            "timezone": "UTC",
            "email": "alice-redirect@example.com",
            "password": "pw123456",
        },
        follow_redirects=False,
    )

    assert resp.status_code == 303
    assert resp.headers["location"] == "/dashboard"


@pytest.mark.asyncio
async def test_authenticated_user_gets_redirected_to_dashboard_from_login_and_signup(
    client: AsyncClient,
) -> None:
    signup_resp = await client.post(
        "/signup",
        data={
            "first_name": "Bob",
            "last_name": "Example",
            "timezone": "UTC",
            "email": "bob-redirect@example.com",
            "password": "pw123456",
        },
        follow_redirects=False,
    )
    assert signup_resp.status_code == 303

    login_form_resp = await client.get("/login", follow_redirects=False)
    assert login_form_resp.status_code == 303
    assert login_form_resp.headers["location"] == "/dashboard"

    signup_form_resp = await client.get("/signup", follow_redirects=False)
    assert signup_form_resp.status_code == 303
    assert signup_form_resp.headers["location"] == "/dashboard"

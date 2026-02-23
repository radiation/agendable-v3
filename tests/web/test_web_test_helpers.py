from __future__ import annotations

import pytest
from httpx import AsyncClient

from agendable.testing.web_test_helpers import login_user


@pytest.mark.asyncio
async def test_login_user_falls_back_to_login_for_existing_account(client: AsyncClient) -> None:
    email = "helper-login@example.com"
    password = "pw-helper"

    await login_user(client, email, password)
    await client.post("/logout", follow_redirects=True)

    await login_user(client, email, password)

    dashboard = await client.get("/dashboard")
    assert dashboard.status_code == 200

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

from fastapi.responses import RedirectResponse
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import User

_DEFAULT_TEST_PASSWORD = os.environ.get("AGENDABLE_TEST_DEFAULT_PASSWORD", "pw123456")


@dataclass
class FakeOidcLinkClient:
    userinfo_payload: dict[str, object]

    async def authorize_redirect(
        self,
        request: object,
        redirect_uri: str,
        **kwargs: object,
    ) -> RedirectResponse:
        return RedirectResponse(url="https://idp.example.test/authorize", status_code=302)

    async def authorize_access_token(self, request: object) -> dict[str, str]:
        return {"access_token": "test-token", "id_token": "id-token"}

    async def parse_id_token(self, request: object, token: object) -> dict[str, object]:
        return self.userinfo_payload

    async def userinfo(self, token: object) -> dict[str, object]:
        return self.userinfo_payload


async def signup_and_login(
    client: AsyncClient,
    *,
    first_name: str,
    last_name: str,
    email: str,
    password: str | None = None,
) -> None:
    effective_password = password if password is not None else _DEFAULT_TEST_PASSWORD
    response = await client.post(
        "/signup",
        data={
            "first_name": first_name,
            "last_name": last_name,
            "timezone": "UTC",
            "email": email,
            "password": effective_password,
        },
        follow_redirects=True,
    )
    assert response.status_code == 200


async def get_user_by_email(db_session: AsyncSession, email: str) -> User:
    return (await db_session.execute(select(User).where(User.email == email))).scalar_one()


def enable_oidc_env(monkeypatch: Any) -> None:
    mp = monkeypatch
    mp.setenv("AGENDABLE_OIDC_CLIENT_ID", "test-client")
    mp.setenv("AGENDABLE_OIDC_CLIENT_SECRET", "test-secret")
    mp.setenv("AGENDABLE_OIDC_METADATA_URL", "https://example.com/.well-known/openid-configuration")

from __future__ import annotations

from dataclasses import dataclass

import pytest
from fastapi.responses import RedirectResponse
from httpx import AsyncClient

from agendable.web.routes import auth as auth_routes


@dataclass
class _FakeOidcStartClient:
    last_prompt: str | None = None

    async def authorize_redirect(
        self,
        request: object,
        redirect_uri: str,
        **kwargs: object,
    ) -> RedirectResponse:
        prompt = kwargs.get("prompt")
        if isinstance(prompt, str):
            self.last_prompt = prompt
        else:
            self.last_prompt = None
        return RedirectResponse(url="https://idp.example.test/authorize", status_code=302)


@pytest.mark.asyncio
async def test_oidc_start_defaults_to_select_account_prompt(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENDABLE_OIDC_CLIENT_ID", "test-client")
    monkeypatch.setenv("AGENDABLE_OIDC_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv(
        "AGENDABLE_OIDC_METADATA_URL", "https://example.com/.well-known/openid-configuration"
    )

    fake_client = _FakeOidcStartClient()
    monkeypatch.setattr(auth_routes, "_oidc_oauth_client", lambda: fake_client)

    response = await client.get("/auth/oidc/start", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "https://idp.example.test/authorize"
    assert fake_client.last_prompt == "select_account"


@pytest.mark.asyncio
async def test_oidc_start_omits_prompt_when_configured_empty(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENDABLE_OIDC_CLIENT_ID", "test-client")
    monkeypatch.setenv("AGENDABLE_OIDC_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv(
        "AGENDABLE_OIDC_METADATA_URL", "https://example.com/.well-known/openid-configuration"
    )
    monkeypatch.setenv("AGENDABLE_OIDC_AUTH_PROMPT", "")

    fake_client = _FakeOidcStartClient()
    monkeypatch.setattr(auth_routes, "_oidc_oauth_client", lambda: fake_client)

    response = await client.get("/auth/oidc/start", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "https://idp.example.test/authorize"
    assert fake_client.last_prompt is None


@pytest.mark.asyncio
async def test_oidc_start_uses_configured_custom_prompt(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENDABLE_OIDC_CLIENT_ID", "test-client")
    monkeypatch.setenv("AGENDABLE_OIDC_CLIENT_SECRET", "test-secret")
    monkeypatch.setenv(
        "AGENDABLE_OIDC_METADATA_URL", "https://example.com/.well-known/openid-configuration"
    )
    monkeypatch.setenv("AGENDABLE_OIDC_AUTH_PROMPT", "login")

    fake_client = _FakeOidcStartClient()
    monkeypatch.setattr(auth_routes, "_oidc_oauth_client", lambda: fake_client)

    response = await client.get("/auth/oidc/start", follow_redirects=False)
    assert response.status_code == 302
    assert response.headers["location"] == "https://idp.example.test/authorize"
    assert fake_client.last_prompt == "login"

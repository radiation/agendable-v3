from __future__ import annotations

from dataclasses import dataclass

from authlib.integrations.starlette_client import OAuth

from agendable.settings import get_settings


@dataclass(frozen=True)
class OidcConfig:
    client_id: str
    client_secret: str
    metadata_url: str


def get_oidc_config() -> OidcConfig | None:
    settings = get_settings()
    if (
        settings.oidc_client_id is None
        or settings.oidc_client_secret is None
        or settings.oidc_metadata_url is None
    ):
        return None

    metadata_url = settings.oidc_metadata_url.strip()
    if not metadata_url:
        return None

    return OidcConfig(
        client_id=settings.oidc_client_id,
        client_secret=settings.oidc_client_secret.get_secret_value(),
        metadata_url=metadata_url,
    )


def oidc_enabled() -> bool:
    return get_oidc_config() is not None


def build_oauth() -> OAuth:
    oauth = OAuth()
    cfg = get_oidc_config()
    if cfg is None:
        return oauth

    oauth.register(
        name="oidc",
        client_id=cfg.client_id,
        client_secret=cfg.client_secret,
        server_metadata_url=cfg.metadata_url,
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth

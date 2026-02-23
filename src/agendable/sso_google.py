from __future__ import annotations

from dataclasses import dataclass

from authlib.integrations.starlette_client import OAuth

from agendable.settings import get_settings


@dataclass(frozen=True)
class GoogleConfig:
    client_id: str
    client_secret: str


def get_google_config() -> GoogleConfig | None:
    settings = get_settings()
    if settings.google_client_id is None or settings.google_client_secret is None:
        return None

    return GoogleConfig(
        client_id=settings.google_client_id,
        client_secret=settings.google_client_secret.get_secret_value(),
    )


def google_enabled() -> bool:
    return get_google_config() is not None


def build_oauth() -> OAuth:
    oauth = OAuth()
    settings = get_settings()
    cfg = get_google_config()
    if cfg is None:
        return oauth

    oauth.register(
        name="google",
        client_id=cfg.client_id,
        client_secret=cfg.client_secret,
        server_metadata_url=settings.google_metadata_url,
        client_kwargs={"scope": "openid email profile"},
    )
    return oauth

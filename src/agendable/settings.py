from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENDABLE_", case_sensitive=False)

    database_url: str = "sqlite+aiosqlite:///./agendable.db"

    # Local-dev convenience. In long-lived environments you should run Alembic migrations
    # and set this to false.
    auto_create_db: bool = True

    # Reminder integrations (optional for now)
    slack_webhook_url: SecretStr | None = None

    # Placeholder for future auth/SSO configuration.
    # For example:
    # - OIDC / OAuth: issuer, client_id, client_secret
    # - SAML: metadata_url, entity_id, x509 cert


def get_settings() -> Settings:
    return Settings()

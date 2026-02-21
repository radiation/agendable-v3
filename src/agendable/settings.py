from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="AGENDABLE_", case_sensitive=False)

    database_url: str = "sqlite+aiosqlite:///./agendable.db"

    # For local development
    auto_create_db: bool = False

    # Session cookie auth (MVP). In production, override via env.
    session_secret: SecretStr = SecretStr("dev-insecure-change-me")
    session_cookie_name: str = "agendable_session"

    # Reminder integrations (optional for now)
    slack_webhook_url: SecretStr | None = None

    # Google OIDC (optional)
    google_client_id: str | None = None
    google_client_secret: SecretStr | None = None
    # If set, only allow users with emails in this domain (e.g. "example.com").
    allowed_email_domain: str | None = None


def get_settings() -> Settings:
    return Settings()

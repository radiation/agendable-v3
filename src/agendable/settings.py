from __future__ import annotations

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="AGENDABLE_",
        case_sensitive=False,
        env_file=".env",
        env_file_encoding="utf-8",
    )

    database_url: str = "sqlite+aiosqlite:///./agendable.db"

    # For local development
    auto_create_db: bool = False

    # Session cookie auth (MVP). In production, override via env.
    session_secret: SecretStr = SecretStr("dev-insecure-change-me")
    session_cookie_name: str = "agendable_session"

    # Reminder integrations (optional for now)
    slack_webhook_url: SecretStr | None = None
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    smtp_from_email: str | None = None
    smtp_use_ssl: bool = False
    smtp_use_starttls: bool = True
    smtp_timeout_seconds: float = 10.0
    enable_default_email_reminders: bool = True
    default_email_reminder_minutes_before: int = 60
    reminder_worker_poll_seconds: int = 60

    # OIDC (optional)
    oidc_client_id: str | None = None
    oidc_client_secret: SecretStr | None = None
    oidc_metadata_url: str | None = None
    oidc_debug_logging: bool = False
    # OIDC authorize "prompt" value (e.g. "select_account", "login").
    # Set to empty string to omit prompt from authorize requests.
    oidc_auth_prompt: str | None = "select_account"
    # If set, only allow users with emails in this domain (e.g. "example.com").
    allowed_email_domain: str | None = None

    # If set, this email is auto-promoted to admin on signup/login.
    bootstrap_admin_email: str | None = None


def get_settings() -> Settings:
    return Settings()

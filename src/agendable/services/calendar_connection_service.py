from __future__ import annotations

from agendable.db.models import CalendarProvider, ExternalCalendarConnection, User
from agendable.db.repos import ExternalCalendarConnectionRepository
from agendable.settings import Settings
from agendable.sso.oidc.flow import OidcTokenCapture


def should_capture_google_calendar_token(
    *,
    settings: Settings,
    token_capture: OidcTokenCapture,
) -> bool:
    if not settings.google_calendar_sync_enabled:
        return False

    required_scope = settings.google_calendar_oidc_additional_scope.strip()
    if not required_scope:
        return False

    scope = token_capture.scope or ""
    granted_scopes = set(scope.split())
    return required_scope in granted_scopes


async def upsert_google_primary_calendar_connection(
    *,
    connection_repo: ExternalCalendarConnectionRepository,
    user: User,
    token_capture: OidcTokenCapture,
) -> ExternalCalendarConnection:
    connection = await connection_repo.get_for_user_provider_calendar(
        user_id=user.id,
        provider=CalendarProvider.google,
        external_calendar_id="primary",
    )

    if connection is None:
        connection = ExternalCalendarConnection(
            user_id=user.id,
            provider=CalendarProvider.google,
            external_calendar_id="primary",
        )
        await connection_repo.add(connection)

    connection.access_token = token_capture.access_token
    if token_capture.refresh_token is not None:
        connection.refresh_token = token_capture.refresh_token
    connection.access_token_expires_at = token_capture.expires_at
    connection.scope = token_capture.scope
    connection.is_enabled = True

    return connection

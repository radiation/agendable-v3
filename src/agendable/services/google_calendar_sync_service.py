from __future__ import annotations

import logging
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol, cast

import httpx
from sqlalchemy import text

from agendable.db.models import (
    CalendarProvider,
    ExternalCalendarConnection,
    ExternalCalendarEventMirror,
    MeetingSeries,
)
from agendable.db.repos import (
    ExternalCalendarConnectionRepository,
    ExternalCalendarEventMirrorRepository,
    MeetingSeriesRepository,
)
from agendable.services.external_calendar_api import (
    ExternalCalendarAuth,
    ExternalCalendarClient,
    ExternalCalendarEvent,
    ExternalCalendarSyncBatch,
    ExternalRecurringEventDetails,
)
from agendable.settings import Settings

logger = logging.getLogger(__name__)

_GOOGLE_OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"

_GOOGLE_CALENDAR_WRITE_SCOPES = frozenset(
    {
        "https://www.googleapis.com/auth/calendar",
        "https://www.googleapis.com/auth/calendar.events",
        "https://www.googleapis.com/auth/calendar.events.owned",
    }
)


class CalendarEventMapper(Protocol):
    async def map_mirrors(
        self,
        *,
        connection: ExternalCalendarConnection,
        mirrors: list[ExternalCalendarEventMirror],
        recurring_event_details_by_id: dict[str, ExternalRecurringEventDetails],
    ) -> int: ...


class GoogleCalendarSyncService:
    def __init__(
        self,
        *,
        connection_repo: ExternalCalendarConnectionRepository,
        event_mirror_repo: ExternalCalendarEventMirrorRepository,
        series_repo: MeetingSeriesRepository | None = None,
        calendar_client: ExternalCalendarClient,
        event_mapper: CalendarEventMapper | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.connection_repo = connection_repo
        self.event_mirror_repo = event_mirror_repo
        self.series_repo = series_repo or MeetingSeriesRepository(event_mirror_repo.session)
        self.calendar_client = calendar_client
        self.event_mapper = event_mapper
        self.settings = settings

    @classmethod
    def from_repositories(
        cls,
        *,
        connection_repo: ExternalCalendarConnectionRepository,
        event_mirror_repo: ExternalCalendarEventMirrorRepository,
        calendar_client: ExternalCalendarClient,
        event_mapper: CalendarEventMapper | None = None,
        settings: Settings | None = None,
    ) -> GoogleCalendarSyncService:
        return cls(
            connection_repo=connection_repo,
            event_mirror_repo=event_mirror_repo,
            series_repo=MeetingSeriesRepository(event_mirror_repo.session),
            calendar_client=calendar_client,
            event_mapper=event_mapper,
            settings=settings,
        )

    async def sync_connection(self, connection: ExternalCalendarConnection) -> int:
        refreshed_preflight = await self._maybe_refresh_google_access_token(connection)
        if refreshed_preflight:
            await self.connection_repo.commit()

        lock_acquired = await self._try_acquire_connection_sync_lock(connection)
        if not lock_acquired:
            logger.info(
                "google calendar sync skipped: connection lock not acquired connection_id=%s",
                connection.id,
            )
            return 0

        access_token = self._require_access_token(connection)
        auth = ExternalCalendarAuth(
            access_token=access_token,
            refresh_token=connection.refresh_token,
        )
        sync_token_for_request = await self._resolve_sync_token_for_request(connection)

        batch: ExternalCalendarSyncBatch

        try:
            batch = await self.calendar_client.list_events(
                auth=auth,
                calendar_id=connection.external_calendar_id,
                sync_token=sync_token_for_request,
            )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code

            # 401 typically indicates an expired/revoked access token.
            if status == 401 and await self._refresh_google_access_token(connection):
                # Persist the refreshed token immediately. This ensures access-token
                # updates aren't lost if the sync fails later and the caller doesn't
                # commit (e.g. manual sync routes that catch exceptions).
                await self.connection_repo.commit()

                # Re-acquire the per-connection sync lock, since the Postgres
                # advisory xact lock is released by commits.
                lock_acquired = await self._try_acquire_connection_sync_lock(connection)
                if not lock_acquired:
                    logger.info(
                        "google calendar sync skipped: connection lock not reacquired after refresh connection_id=%s",
                        connection.id,
                    )
                    return 0

                access_token = self._require_access_token(connection)
                auth = ExternalCalendarAuth(
                    access_token=access_token,
                    refresh_token=connection.refresh_token,
                )
                batch = await self.calendar_client.list_events(
                    auth=auth,
                    calendar_id=connection.external_calendar_id,
                    sync_token=sync_token_for_request,
                )
            # 410 indicates the sync token is no longer valid; clear it and do a bootstrap sync.
            elif status == 410 and sync_token_for_request is not None:
                logger.info(
                    "google calendar sync cursor expired; resetting connection_id=%s",
                    connection.id,
                )
                connection.sync_token = None
                batch = await self.calendar_client.list_events(
                    auth=auth,
                    calendar_id=connection.external_calendar_id,
                    sync_token=None,
                )
            else:
                raise

        touched_mirrors = await self._upsert_mirror_events(
            connection=connection, events=batch.events
        )
        await self._map_mirrors_and_write_back(connection=connection, mirrors=touched_mirrors)

        self._touch_successful_sync(connection, next_sync_token=batch.next_sync_token)
        await self.connection_repo.commit()
        return len(batch.events)

    async def _try_acquire_connection_sync_lock(
        self,
        connection: ExternalCalendarConnection,
    ) -> bool:
        """Acquire a best-effort per-connection sync lock.

        Prevents overlapping syncs (e.g. worker loop + web-trigger) from doing duplicate work.

        Uses a Postgres advisory *transaction* lock when running on Postgres.
        For other dialects (e.g. SQLite in tests), this is a no-op and always returns True.
        """

        session = self.event_mirror_repo.session
        try:
            bind = session.get_bind()
        except Exception:
            return True

        if getattr(getattr(bind, "dialect", None), "name", None) != "postgresql":
            return True

        # Use a signed 63-bit integer key derived from the UUID.
        key = int(connection.id.int % (2**63))
        result = await session.execute(
            text("SELECT pg_try_advisory_xact_lock(:key)"),
            {"key": key},
        )
        acquired = result.scalar_one()
        return bool(acquired)

    def _require_access_token(self, connection: ExternalCalendarConnection) -> str:
        if not connection.access_token:
            raise ValueError("Calendar connection is missing an access token")
        return connection.access_token

    async def _maybe_refresh_google_access_token(
        self, connection: ExternalCalendarConnection
    ) -> bool:
        expires_at = connection.access_token_expires_at
        if expires_at is None:
            return False

        now = datetime.now(UTC)
        # Refresh a little early to avoid races.
        if expires_at > now + timedelta(seconds=60):
            return False

        return await self._refresh_google_access_token(connection)

    async def _refresh_google_access_token(self, connection: ExternalCalendarConnection) -> bool:
        """Refresh the Google access token in-place.

        Returns True when a new access token was obtained and applied.
        """

        refresh_token = connection.refresh_token
        if not refresh_token:
            return False

        creds = self._google_refresh_client_credentials()
        if creds is None:
            return False

        client_id, client_secret = creds
        data = self._google_refresh_request_data(
            client_id=client_id,
            client_secret=client_secret,
            refresh_token=refresh_token,
        )

        response = await self._post_google_token_refresh(data)
        if response.status_code >= 400:
            self._log_google_auth_update_failed(connection_id=connection.id, response=response)
            return False

        payload = self._parse_json_dict_response(response)
        if payload is None:
            return False

        return self._apply_google_token_refresh_payload(connection, payload)

    def _google_refresh_client_credentials(self) -> tuple[str, str] | None:
        settings = self.settings
        if (
            settings is None
            or settings.oidc_client_id is None
            or settings.oidc_client_secret is None
        ):
            return None
        return settings.oidc_client_id, settings.oidc_client_secret.get_secret_value()

    def _google_refresh_request_data(
        self,
        *,
        client_id: str,
        client_secret: str,
        refresh_token: str,
    ) -> dict[str, str]:
        return {
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        }

    async def _post_google_token_refresh(self, data: dict[str, str]) -> httpx.Response:
        async with httpx.AsyncClient(timeout=15.0) as client:
            return await client.post(_GOOGLE_OAUTH_TOKEN_URL, data=data)

    def _parse_json_dict_response(self, response: httpx.Response) -> dict[str, Any] | None:
        try:
            raw_payload: object = response.json()
        except Exception:
            return None
        if not isinstance(raw_payload, dict):
            return None
        return cast(dict[str, Any], raw_payload)

    def _log_google_auth_update_failed(
        self,
        *,
        connection_id: object,
        response: httpx.Response,
    ) -> None:
        payload_type: str | None
        try:
            parsed: object = response.json()
        except Exception:
            payload_type = None
        else:
            payload_type = type(parsed).__name__

        logger.warning(
            "google auth update failed connection_id=%s status=%s payload_type=%s",
            connection_id,
            response.status_code,
            payload_type,
        )

    def _apply_google_token_refresh_payload(
        self,
        connection: ExternalCalendarConnection,
        payload: dict[str, Any],
    ) -> bool:
        access_token = payload.get("access_token")
        if not isinstance(access_token, str) or not access_token.strip():
            return False

        connection.access_token = access_token.strip()

        # Google usually does not rotate refresh tokens on refresh, but keep it if provided.
        new_refresh = payload.get("refresh_token")
        if isinstance(new_refresh, str) and new_refresh.strip():
            connection.refresh_token = new_refresh.strip()

        expires_in = payload.get("expires_in")
        if isinstance(expires_in, int | float):
            connection.access_token_expires_at = datetime.now(UTC).replace(
                microsecond=0
            ) + timedelta(seconds=float(expires_in))

        return True

    async def _resolve_sync_token_for_request(
        self, connection: ExternalCalendarConnection
    ) -> str | None:
        sync_token_for_request = connection.sync_token
        if sync_token_for_request is None:
            return None

        has_mirrored_events = await self.event_mirror_repo.has_any_for_connection(connection.id)
        if has_mirrored_events:
            return sync_token_for_request
        return None

    async def _upsert_mirror_events(
        self,
        *,
        connection: ExternalCalendarConnection,
        events: Sequence[ExternalCalendarEvent],
    ) -> list[ExternalCalendarEventMirror]:
        touched_mirrors: list[ExternalCalendarEventMirror] = []
        for event in events:
            touched_mirrors.append(
                await self._upsert_mirror_event(connection=connection, event=event)
            )
        return touched_mirrors

    async def _map_mirrors_and_write_back(
        self,
        *,
        connection: ExternalCalendarConnection,
        mirrors: list[ExternalCalendarEventMirror],
    ) -> None:
        if self.event_mapper is None or not mirrors:
            return

        recurring_event_ids = self._recurring_event_ids(mirrors)
        details_by_id = await self._fetch_recurring_event_details(
            connection=connection,
            recurring_event_ids=recurring_event_ids,
        )

        await self.event_mapper.map_mirrors(
            connection=connection,
            mirrors=mirrors,
            recurring_event_details_by_id=details_by_id,
        )

        await self._maybe_write_back_backlinks(
            connection=connection,
            mirrors=mirrors,
            recurring_event_ids=sorted(recurring_event_ids),
        )

    async def _maybe_write_back_backlinks(
        self,
        *,
        connection: ExternalCalendarConnection,
        mirrors: Sequence[ExternalCalendarEventMirror],
        recurring_event_ids: list[str],
    ) -> None:
        settings = self.settings
        if settings is None:
            return

        target = settings.google_calendar_backlink_target
        if target in {"series", "both"}:
            await self._maybe_write_back_recurring_backlinks(
                connection=connection,
                recurring_event_ids=recurring_event_ids,
            )

        if target in {"occurrence", "both"}:
            await self._maybe_write_back_occurrence_backlinks(
                connection=connection,
                mirrors=mirrors,
            )

    def _recurring_event_ids(self, mirrors: Sequence[ExternalCalendarEventMirror]) -> set[str]:
        out: set[str] = set()
        for mirror in mirrors:
            recurring_id = mirror.external_recurring_event_id
            if recurring_id:
                out.add(recurring_id)
        return out

    async def _fetch_recurring_event_details(
        self,
        *,
        connection: ExternalCalendarConnection,
        recurring_event_ids: set[str],
    ) -> dict[str, ExternalRecurringEventDetails]:
        if not recurring_event_ids:
            return {}

        auth = ExternalCalendarAuth(
            access_token=self._require_access_token(connection),
            refresh_token=connection.refresh_token,
        )
        details_by_id: dict[str, ExternalRecurringEventDetails] = {}
        for recurring_event_id in recurring_event_ids:
            details = await self.calendar_client.get_recurring_event_details(
                auth=auth,
                calendar_id=connection.external_calendar_id,
                recurring_event_id=recurring_event_id,
            )
            if details is not None:
                details_by_id[recurring_event_id] = details
        return details_by_id

    def _touch_successful_sync(
        self,
        connection: ExternalCalendarConnection,
        *,
        next_sync_token: str | None,
    ) -> None:
        connection.sync_token = next_sync_token
        connection.last_synced_at = datetime.now(UTC)
        connection.last_sync_error_code = None
        connection.last_sync_error_at = None

    async def _maybe_write_back_recurring_backlinks(
        self,
        *,
        connection: ExternalCalendarConnection,
        recurring_event_ids: list[str],
    ) -> None:
        base_url = self._resolve_backlink_base_url(connection)
        if base_url is None or not recurring_event_ids:
            return
        await self._write_back_recurring_backlinks(
            connection=connection,
            recurring_event_ids=recurring_event_ids,
            base_url=base_url,
        )

    async def _maybe_write_back_occurrence_backlinks(
        self,
        *,
        connection: ExternalCalendarConnection,
        mirrors: Sequence[ExternalCalendarEventMirror],
    ) -> None:
        base_url = self._resolve_backlink_base_url(connection)
        if base_url is None:
            return

        auth = ExternalCalendarAuth(
            access_token=self._require_access_token(connection),
            refresh_token=connection.refresh_token,
        )
        for mirror in mirrors:
            if mirror.external_event_id is None or mirror.linked_occurrence_id is None:
                continue
            if mirror.external_status == "cancelled":
                continue

            occurrence_url = f"{base_url}/occurrences/{mirror.linked_occurrence_id}"
            await self.calendar_client.upsert_event_backlink(
                auth=auth,
                calendar_id=connection.external_calendar_id,
                event_id=mirror.external_event_id,
                agendable_occurrence_id=str(mirror.linked_occurrence_id),
                agendable_occurrence_url=occurrence_url,
            )

    def _resolve_backlink_base_url(self, connection: ExternalCalendarConnection) -> str | None:
        settings = self.settings
        if settings is None or not settings.google_calendar_backlink_enabled:
            return None

        if not self._has_google_calendar_write_scope(connection.scope):
            return None

        base_url = (settings.public_base_url or "").strip().rstrip("/")
        if not base_url:
            return None
        return base_url

    def _has_google_calendar_write_scope(self, scope: str | None) -> bool:
        if not scope:
            return False
        scopes = set(scope.split())
        return bool(scopes.intersection(_GOOGLE_CALENDAR_WRITE_SCOPES))

    async def _write_back_recurring_backlinks(
        self,
        *,
        connection: ExternalCalendarConnection,
        recurring_event_ids: Sequence[str],
        base_url: str,
    ) -> None:
        auth = ExternalCalendarAuth(
            access_token=self._require_access_token(connection),
            refresh_token=connection.refresh_token,
        )
        for recurring_event_id in recurring_event_ids:
            series = await self._find_series_for_recurring_event(
                connection_id=connection.id,
                recurring_event_id=recurring_event_id,
            )
            if series is None:
                continue

            series_url = f"{base_url}/series/{series.id}"
            await self.calendar_client.upsert_recurring_event_backlink(
                auth=auth,
                calendar_id=connection.external_calendar_id,
                recurring_event_id=recurring_event_id,
                agendable_series_id=str(series.id),
                agendable_series_url=series_url,
            )

    async def _find_series_for_recurring_event(
        self,
        *,
        connection_id: uuid.UUID,
        recurring_event_id: str,
    ) -> MeetingSeries | None:
        return await self.series_repo.find_for_connection_recurring_event(
            connection_id=connection_id,
            recurring_event_id=recurring_event_id,
        )

    async def sync_all_enabled_connections(self) -> int:
        synced_event_count = 0
        connections = await self.connection_repo.list_enabled_for_provider(
            provider=CalendarProvider.google,
        )
        for connection in connections:
            try:
                synced_event_count += await self.sync_connection(connection)
            except httpx.HTTPStatusError as exc:
                status = exc.response.status_code
                if status == 401 and not connection.refresh_token:
                    connection.last_sync_error_code = "needs_reauth"
                else:
                    connection.last_sync_error_code = f"http_{status}"
                connection.last_sync_error_at = datetime.now(UTC)
                await self.connection_repo.commit()
                logger.warning(
                    "google calendar sync failed for connection_id=%s status=%s",
                    connection.id,
                    status,
                )
            except Exception:
                connection.last_sync_error_code = "error"
                connection.last_sync_error_at = datetime.now(UTC)
                await self.connection_repo.commit()
                logger.exception(
                    "google calendar sync failed for connection_id=%s",
                    connection.id,
                )
        return synced_event_count

    async def _upsert_mirror_event(
        self,
        *,
        connection: ExternalCalendarConnection,
        event: ExternalCalendarEvent,
    ) -> ExternalCalendarEventMirror:
        existing = await self.event_mirror_repo.get_or_create_for_connection_event(
            connection_id=connection.id,
            external_event_id=event.event_id,
        )

        existing.external_recurring_event_id = event.recurring_event_id
        existing.external_status = event.status
        existing.etag = event.etag
        existing.summary = event.summary
        existing.start_at = event.start_at
        existing.end_at = event.end_at
        existing.is_all_day = event.is_all_day
        existing.external_updated_at = event.external_updated_at
        existing.last_seen_at = datetime.now(UTC)
        await self.event_mirror_repo.session.flush()
        return existing


__all__ = [
    "ExternalCalendarClient",
    "ExternalCalendarEvent",
    "ExternalCalendarSyncBatch",
    "ExternalRecurringEventDetails",
    "GoogleCalendarSyncService",
]

from __future__ import annotations

import uuid
from contextlib import suppress
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import ExternalCalendarEventMirror, MeetingOccurrence
from agendable.db.repos.base import BaseRepository


class ExternalCalendarEventMirrorRepository(BaseRepository[ExternalCalendarEventMirror]):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session, ExternalCalendarEventMirror)

    async def get_for_connection_event(
        self,
        *,
        connection_id: uuid.UUID,
        external_event_id: str,
    ) -> ExternalCalendarEventMirror | None:
        result = await self.session.execute(
            select(ExternalCalendarEventMirror).where(
                ExternalCalendarEventMirror.connection_id == connection_id,
                ExternalCalendarEventMirror.external_event_id == external_event_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_or_create_for_connection_event(
        self,
        *,
        connection_id: uuid.UUID,
        external_event_id: str,
    ) -> ExternalCalendarEventMirror:
        existing = await self.get_for_connection_event(
            connection_id=connection_id,
            external_event_id=external_event_id,
        )
        if existing is not None:
            return existing

        mirror = ExternalCalendarEventMirror(
            connection_id=connection_id,
            external_event_id=external_event_id,
        )

        try:
            async with self.session.begin_nested():
                self.session.add(mirror)
                await self.session.flush([mirror])
            return mirror
        except IntegrityError:
            # Another worker likely inserted the row after our initial SELECT.
            # The savepoint rollback keeps the outer transaction usable.
            with suppress(Exception):
                self.session.expunge(mirror)

        existing_after = await self.get_for_connection_event(
            connection_id=connection_id,
            external_event_id=external_event_id,
        )
        if existing_after is None:
            raise
        return existing_after

    async def touch_seen(self, mirror: ExternalCalendarEventMirror) -> ExternalCalendarEventMirror:
        mirror.last_seen_at = datetime.now(UTC)
        await self.session.flush()
        return mirror

    async def has_any_for_connection(self, connection_id: uuid.UUID) -> bool:
        result = await self.session.execute(
            select(ExternalCalendarEventMirror.id)
            .where(ExternalCalendarEventMirror.connection_id == connection_id)
            .limit(1)
        )
        return result.scalar_one_or_none() is not None

    async def list_for_connection_recurring_event(
        self,
        *,
        connection_id: uuid.UUID,
        recurring_event_id: str,
    ) -> list[ExternalCalendarEventMirror]:
        result = await self.session.execute(
            select(ExternalCalendarEventMirror)
            .where(
                ExternalCalendarEventMirror.connection_id == connection_id,
                ExternalCalendarEventMirror.external_recurring_event_id == recurring_event_id,
            )
            .order_by(ExternalCalendarEventMirror.created_at.asc())
        )
        return list(result.scalars().all())

    async def list_linked_to_series(
        self,
        *,
        series_id: uuid.UUID,
    ) -> list[ExternalCalendarEventMirror]:
        result = await self.session.execute(
            select(ExternalCalendarEventMirror)
            .join(
                MeetingOccurrence,
                ExternalCalendarEventMirror.linked_occurrence_id == MeetingOccurrence.id,
            )
            .where(MeetingOccurrence.series_id == series_id)
        )
        return list(result.scalars().all())

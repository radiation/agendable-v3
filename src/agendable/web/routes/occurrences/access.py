from __future__ import annotations

import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agendable.db.models import (
    MeetingOccurrence,
    MeetingOccurrenceAttendee,
    MeetingSeries,
    User,
)
from agendable.db.repos import (
    MeetingOccurrenceRepository,
    MeetingSeriesRepository,
    UserRepository,
)


def ensure_occurrence_writable(occurrence_id: uuid.UUID, is_completed: bool) -> None:
    if is_completed:
        raise HTTPException(
            status_code=400,
            detail=f"Meeting {occurrence_id} is completed and read-only",
        )


def normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


async def get_owned_occurrence(
    session: AsyncSession,
    occurrence_id: uuid.UUID,
    owner_user_id: uuid.UUID,
) -> tuple[MeetingOccurrence, MeetingSeries]:
    occ_repo = MeetingOccurrenceRepository(session)
    occurrence = await occ_repo.get_by_id(occurrence_id)
    if occurrence is None:
        raise HTTPException(status_code=404)

    series_repo = MeetingSeriesRepository(session)
    series = await series_repo.get_for_owner(occurrence.series_id, owner_user_id)
    if series is None:
        raise HTTPException(status_code=404)

    return occurrence, series


async def get_accessible_occurrence(
    session: AsyncSession,
    occurrence_id: uuid.UUID,
    user_id: uuid.UUID,
) -> tuple[MeetingOccurrence, MeetingSeries]:
    occ_repo = MeetingOccurrenceRepository(session)
    occurrence = await occ_repo.get_by_id(occurrence_id)
    if occurrence is None:
        raise HTTPException(status_code=404)

    series_repo = MeetingSeriesRepository(session)
    owner_series = await series_repo.get_for_owner(occurrence.series_id, user_id)
    if owner_series is not None:
        return occurrence, owner_series

    attendee_link = (
        await session.execute(
            select(MeetingOccurrenceAttendee).where(
                MeetingOccurrenceAttendee.occurrence_id == occurrence.id,
                MeetingOccurrenceAttendee.user_id == user_id,
            )
        )
    ).scalar_one_or_none()
    if attendee_link is None:
        raise HTTPException(status_code=404)

    series = (
        await session.execute(select(MeetingSeries).where(MeetingSeries.id == occurrence.series_id))
    ).scalar_one_or_none()
    if series is None:
        raise HTTPException(status_code=404)

    return occurrence, series


async def list_occurrence_attendee_users(
    session: AsyncSession,
    occurrence_id: uuid.UUID,
    current_user: User,
) -> list[User]:
    attendee_links = list(
        (
            await session.execute(
                select(MeetingOccurrenceAttendee)
                .options(selectinload(MeetingOccurrenceAttendee.user))
                .where(MeetingOccurrenceAttendee.occurrence_id == occurrence_id)
            )
        )
        .scalars()
        .all()
    )

    attendee_users = [current_user]
    attendee_user_ids: set[uuid.UUID] = {current_user.id}
    for link in attendee_links:
        if link.user_id in attendee_user_ids:
            continue
        attendee_users.append(link.user)
        attendee_user_ids.add(link.user_id)

    return attendee_users


async def validate_task_assignee(
    *,
    session: AsyncSession,
    occurrence_id: uuid.UUID,
    series_owner_user_id: uuid.UUID,
    assignee_id: uuid.UUID,
    task_form_errors: dict[str, str],
) -> None:
    users_repo = UserRepository(session)
    assignee = await users_repo.get_by_id(assignee_id)
    if assignee is None:
        task_form_errors["assigned_user_id"] = "Choose a valid assignee."
        return

    if assignee_id == series_owner_user_id:
        return

    attendee_link = (
        await session.execute(
            select(MeetingOccurrenceAttendee).where(
                MeetingOccurrenceAttendee.occurrence_id == occurrence_id,
                MeetingOccurrenceAttendee.user_id == assignee_id,
            )
        )
    ).scalar_one_or_none()
    if attendee_link is None:
        task_form_errors["assigned_user_id"] = "Assignee must be a meeting attendee."

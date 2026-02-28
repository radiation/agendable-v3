from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import MeetingOccurrence, MeetingOccurrenceAttendee, MeetingSeries, User
from agendable.db.repos import MeetingOccurrenceRepository, MeetingSeriesRepository
from agendable.recurrence import build_rrule
from agendable.web.routes.common import recurrence_label, templates

VALID_RECURRENCE_FREQS = {"DAILY", "WEEKLY", "MONTHLY"}


def normalize_recurrence_freq(raw: str) -> str:
    normalized = raw.strip().upper()
    if normalized in VALID_RECURRENCE_FREQS:
        return normalized
    return "DAILY"


def validate_create_series_inputs(
    *,
    reminder_minutes_before: int,
    generate_count: int,
    recurrence_interval: int,
) -> None:
    if reminder_minutes_before < 0 or reminder_minutes_before > 60 * 24 * 30:
        raise HTTPException(
            status_code=400,
            detail="reminder_minutes_before must be between 0 and 43200",
        )

    if generate_count < 1 or generate_count > 200:
        raise HTTPException(status_code=400, detail="generate_count must be between 1 and 200")

    if recurrence_interval < 1 or recurrence_interval > 365:
        raise HTTPException(status_code=400, detail="recurrence_interval must be between 1 and 365")


def parse_monthly_bymonthday(monthly_bymonthday: str | None) -> int | None:
    if monthly_bymonthday is None or not monthly_bymonthday.strip():
        return None

    try:
        return int(monthly_bymonthday)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid monthly day") from exc


def parse_attendee_emails(attendee_emails: str) -> list[str]:
    parsed = [email.strip().lower() for email in attendee_emails.replace("\n", ",").split(",")]
    unique: list[str] = []
    for email in parsed:
        if not email:
            continue
        if email not in unique:
            unique.append(email)
    return unique


def autocomplete_needle(*, q: str, attendee_emails: str) -> str:
    query = q.strip()
    if query:
        return query.lower()

    raw = attendee_emails.strip()
    if not raw:
        return ""

    token = raw.split(",")[-1].strip()
    return token.lower()


def build_normalized_rrule(
    *,
    recurrence_freq: str,
    recurrence_interval: int,
    dtstart: datetime,
    weekly_byday: list[str],
    monthly_mode: str,
    monthly_bymonthday: int | None,
    monthly_byday: str | None,
    monthly_bysetpos: list[int],
) -> str:
    try:
        return build_rrule(
            freq=recurrence_freq,
            interval=recurrence_interval,
            dtstart=dtstart,
            weekly_byday=weekly_byday,
            monthly_mode=monthly_mode,
            monthly_bymonthday=monthly_bymonthday,
            monthly_byday=monthly_byday,
            monthly_bysetpos=monthly_bysetpos,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid recurrence settings") from exc


async def render_series_detail(
    *,
    request: Request,
    session: AsyncSession,
    series_id: uuid.UUID,
    current_user: User,
    status_code: int = 200,
    attendee_form: dict[str, str] | None = None,
    attendee_form_errors: dict[str, str] | None = None,
) -> HTMLResponse:
    series_repo = MeetingSeriesRepository(session)
    series = await series_repo.get_for_owner(series_id, current_user.id)
    if series is None:
        raise HTTPException(status_code=404)

    occ_repo = MeetingOccurrenceRepository(session)
    occurrences = await occ_repo.list_for_series(series_id)

    active_occurrence: MeetingOccurrence | None = None
    now = datetime.now(UTC)
    for occurrence in occurrences:
        scheduled_at = occurrence.scheduled_at
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=UTC)
        if scheduled_at >= now:
            active_occurrence = occurrence
            break
    if active_occurrence is None and occurrences:
        active_occurrence = occurrences[-1]

    selected_attendee_form = {"email": ""}
    if attendee_form is not None:
        selected_attendee_form.update(attendee_form)

    return templates.TemplateResponse(
        request,
        "series_detail.html",
        {
            "series": series,
            "recurrence_label": recurrence_label(
                recurrence_rrule=series.recurrence_rrule,
                recurrence_dtstart=series.recurrence_dtstart,
                recurrence_timezone=series.recurrence_timezone,
                default_interval_days=series.default_interval_days,
            ),
            "occurrences": occurrences,
            "active_occurrence": active_occurrence,
            "attendee_form": selected_attendee_form,
            "attendee_form_errors": attendee_form_errors or {},
            "current_user": current_user,
        },
        status_code=status_code,
    )


async def get_owned_series_or_404(
    session: AsyncSession,
    series_id: uuid.UUID,
    owner_user_id: uuid.UUID,
) -> MeetingSeries:
    series_repo = MeetingSeriesRepository(session)
    series = await series_repo.get_for_owner(series_id, owner_user_id)
    if series is None:
        raise HTTPException(status_code=404)
    return series


async def resolve_series_attendee_user(
    session: AsyncSession,
    email: str,
) -> User | None:
    result = await session.execute(select(User).where(User.email == email))
    return result.scalar_one_or_none()


async def existing_attendee_occurrence_ids(
    *,
    session: AsyncSession,
    attendee_user_id: uuid.UUID,
    occurrence_ids: list[uuid.UUID],
) -> set[uuid.UUID]:
    if not occurrence_ids:
        return set()

    existing_links = (
        await session.execute(
            select(MeetingOccurrenceAttendee.occurrence_id).where(
                MeetingOccurrenceAttendee.user_id == attendee_user_id,
                MeetingOccurrenceAttendee.occurrence_id.in_(occurrence_ids),
            )
        )
    ).scalars()
    return set(existing_links)


def add_missing_attendee_links(
    *,
    session: AsyncSession,
    attendee_user_id: uuid.UUID,
    occurrence_ids: list[uuid.UUID],
    existing_occurrence_ids: set[uuid.UUID],
) -> int:
    added_count = 0
    for occurrence_id in occurrence_ids:
        if occurrence_id in existing_occurrence_ids:
            continue
        session.add(
            MeetingOccurrenceAttendee(occurrence_id=occurrence_id, user_id=attendee_user_id)
        )
        added_count += 1
    return added_count

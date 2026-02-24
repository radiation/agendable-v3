from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from agendable.auth import require_user
from agendable.db import get_session
from agendable.db.models import MeetingOccurrence, User
from agendable.db.repos import MeetingOccurrenceRepository, MeetingSeriesRepository
from agendable.logging_config import log_with_fields
from agendable.recurrence import build_rrule
from agendable.reminders import build_default_email_reminder
from agendable.services import create_series_with_occurrences
from agendable.settings import get_settings
from agendable.web.routes.common import (
    parse_date,
    parse_dt,
    parse_time,
    parse_timezone,
    recurrence_label,
    templates,
)

router = APIRouter()
logger = logging.getLogger("agendable.series")


@router.get("/", response_class=Response)
async def index(request: Request, session: AsyncSession = Depends(get_session)) -> Response:
    try:
        current_user = await require_user(request, session)
    except HTTPException:
        return RedirectResponse(url="/login", status_code=303)

    series_repo = MeetingSeriesRepository(session)
    series = await series_repo.list_for_owner(current_user.id)
    series_recurrence = {
        s.id: recurrence_label(
            recurrence_rrule=s.recurrence_rrule,
            recurrence_dtstart=s.recurrence_dtstart,
            recurrence_timezone=s.recurrence_timezone,
            default_interval_days=s.default_interval_days,
        )
        for s in series
    }

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "series": series,
            "series_recurrence": series_recurrence,
            "current_user": current_user,
        },
    )


@router.post("/series", response_class=RedirectResponse)
async def create_series(
    title: str = Form(...),
    reminder_minutes_before: int = Form(60),
    recurrence_start_date: str = Form(...),
    recurrence_time: str = Form(...),
    recurrence_timezone: str = Form("UTC"),
    recurrence_freq: str = Form(...),
    recurrence_interval: int = Form(1),
    weekly_byday: list[str] = Form([]),
    monthly_mode: str = Form("monthday"),
    monthly_bymonthday: str | None = Form(None),
    monthly_byday: str | None = Form(None),
    monthly_bysetpos: list[int] = Form([]),
    generate_count: int = Form(10),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> RedirectResponse:
    if reminder_minutes_before < 0 or reminder_minutes_before > 60 * 24 * 30:
        raise HTTPException(
            status_code=400,
            detail="reminder_minutes_before must be between 0 and 43200",
        )

    if generate_count < 1 or generate_count > 200:
        raise HTTPException(status_code=400, detail="generate_count must be between 1 and 200")

    if recurrence_interval < 1 or recurrence_interval > 365:
        raise HTTPException(status_code=400, detail="recurrence_interval must be between 1 and 365")

    start_date = parse_date(recurrence_start_date)
    start_time = parse_time(recurrence_time)
    tz = parse_timezone(recurrence_timezone)
    dtstart = datetime.combine(start_date, start_time).replace(tzinfo=tz)

    bymonthday: int | None
    if monthly_bymonthday is None or not monthly_bymonthday.strip():
        bymonthday = None
    else:
        try:
            bymonthday = int(monthly_bymonthday)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="Invalid monthly day") from exc

    try:
        normalized_rrule = build_rrule(
            freq=recurrence_freq,
            interval=recurrence_interval,
            dtstart=dtstart,
            weekly_byday=weekly_byday,
            monthly_mode=monthly_mode,
            monthly_bymonthday=bymonthday,
            monthly_byday=monthly_byday,
            monthly_bysetpos=monthly_bysetpos,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid recurrence settings") from exc

    settings = get_settings()
    try:
        series, occurrences = await create_series_with_occurrences(
            session,
            owner_user_id=current_user.id,
            title=title,
            reminder_minutes_before=reminder_minutes_before,
            recurrence_rrule=normalized_rrule,
            recurrence_dtstart=dtstart,
            recurrence_timezone=recurrence_timezone,
            generate_count=generate_count,
            settings=settings,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    log_with_fields(
        logger,
        logging.INFO,
        "series created",
        user_id=current_user.id,
        series_id=series.id,
        occurrence_count=len(occurrences),
        reminder_minutes_before=series.reminder_minutes_before,
        recurrence_freq=recurrence_freq,
        recurrence_interval=recurrence_interval,
    )

    return RedirectResponse(url="/", status_code=303)


@router.get("/series/{series_id}", response_class=HTMLResponse)
async def series_detail(
    request: Request,
    series_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> HTMLResponse:
    series_repo = MeetingSeriesRepository(session)
    series = await series_repo.get_for_owner(series_id, current_user.id)
    if series is None:
        raise HTTPException(status_code=404)

    occ_repo = MeetingOccurrenceRepository(session)
    occurrences = await occ_repo.list_for_series(series_id)

    active_occurrence: MeetingOccurrence | None = None
    now = datetime.now(UTC)
    for o in occurrences:
        scheduled_at = o.scheduled_at
        if scheduled_at.tzinfo is None:
            scheduled_at = scheduled_at.replace(tzinfo=UTC)
        if scheduled_at >= now:
            active_occurrence = o
            break
    if active_occurrence is None and occurrences:
        active_occurrence = occurrences[-1]

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
            "current_user": current_user,
        },
    )


@router.post("/series/{series_id}/occurrences", response_class=RedirectResponse)
async def create_occurrence(
    series_id: uuid.UUID,
    scheduled_at: str = Form(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> RedirectResponse:
    series_repo = MeetingSeriesRepository(session)
    series = await series_repo.get_for_owner(series_id, current_user.id)
    if series is None:
        raise HTTPException(status_code=404)

    occ = MeetingOccurrence(series_id=series_id, scheduled_at=parse_dt(scheduled_at), notes="")
    session.add(occ)
    await session.flush()

    settings = get_settings()
    if settings.enable_default_email_reminders:
        session.add(
            build_default_email_reminder(
                occurrence_id=occ.id,
                occurrence_scheduled_at=occ.scheduled_at,
                settings=settings,
                lead_minutes_before=series.reminder_minutes_before,
            )
        )

    await session.commit()

    log_with_fields(
        logger,
        logging.INFO,
        "occurrence created",
        user_id=current_user.id,
        series_id=series_id,
        occurrence_id=occ.id,
        scheduled_at=occ.scheduled_at,
    )

    return RedirectResponse(url=f"/series/{series_id}", status_code=303)

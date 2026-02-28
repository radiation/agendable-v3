from __future__ import annotations

import logging
import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from agendable.auth import require_user
from agendable.db import get_session
from agendable.db.models import MeetingOccurrence, MeetingOccurrenceAttendee, User
from agendable.db.repos import MeetingOccurrenceRepository, MeetingSeriesRepository
from agendable.logging_config import log_with_fields
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
from agendable.web.routes.series_helpers import (
    add_missing_attendee_links,
    autocomplete_needle,
    build_normalized_rrule,
    existing_attendee_occurrence_ids,
    get_owned_series_or_404,
    normalize_recurrence_freq,
    parse_attendee_emails,
    parse_monthly_bymonthday,
    render_series_detail,
    resolve_series_attendee_user,
    validate_create_series_inputs,
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
            "selected_recurrence_freq": "DAILY",
        },
    )


@router.get("/series/recurrence-options", response_class=HTMLResponse)
async def series_recurrence_options(
    request: Request,
    recurrence_freq: str = "DAILY",
    current_user: User = Depends(require_user),
) -> HTMLResponse:
    selected = normalize_recurrence_freq(recurrence_freq)
    return templates.TemplateResponse(
        request,
        "partials/series_recurrence_options.html",
        {
            "recurrence_freq": selected,
            "current_user": current_user,
        },
    )


@router.get("/series/attendee-suggestions", response_class=HTMLResponse)
async def series_attendee_suggestions(
    request: Request,
    q: str = "",
    attendee_emails: str = "",
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> HTMLResponse:
    needle = autocomplete_needle(q=q, attendee_emails=attendee_emails)
    users: list[User] = []
    if len(needle) >= 2:
        pattern = f"%{needle}%"
        users = list(
            (
                await session.execute(
                    select(User)
                    .where(
                        User.is_active.is_(True),
                        User.id != current_user.id,
                        or_(
                            func.lower(User.email).like(pattern),
                            func.lower(User.display_name).like(pattern),
                        ),
                    )
                    .order_by(User.display_name.asc(), User.email.asc())
                    .limit(8)
                )
            )
            .scalars()
            .all()
        )

    return templates.TemplateResponse(
        request,
        "partials/series_attendee_suggestions.html",
        {
            "users": users,
            "query": needle,
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
    attendee_emails: str = Form(""),
    generate_count: int = Form(10),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> RedirectResponse:
    validate_create_series_inputs(
        reminder_minutes_before=reminder_minutes_before,
        generate_count=generate_count,
        recurrence_interval=recurrence_interval,
    )

    start_date = parse_date(recurrence_start_date)
    start_time = parse_time(recurrence_time)
    tz = parse_timezone(recurrence_timezone)
    dtstart = datetime.combine(start_date, start_time).replace(tzinfo=tz)

    bymonthday = parse_monthly_bymonthday(monthly_bymonthday)

    normalized_rrule = build_normalized_rrule(
        recurrence_freq=recurrence_freq,
        recurrence_interval=recurrence_interval,
        dtstart=dtstart,
        weekly_byday=weekly_byday,
        monthly_mode=monthly_mode,
        monthly_bymonthday=bymonthday,
        monthly_byday=monthly_byday,
        monthly_bysetpos=monthly_bysetpos,
    )

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

    attendee_user_ids: set[uuid.UUID] = {current_user.id}
    parsed_attendee_emails = parse_attendee_emails(attendee_emails)
    if parsed_attendee_emails:
        attendee_users = (
            (await session.execute(select(User).where(User.email.in_(parsed_attendee_emails))))
            .scalars()
            .all()
        )
        attendee_users_by_email = {user.email.lower(): user for user in attendee_users}
        unknown_attendee_emails = [
            email for email in parsed_attendee_emails if email not in attendee_users_by_email
        ]
        if unknown_attendee_emails:
            raise HTTPException(
                status_code=400,
                detail=("Unknown attendee email(s): " + ", ".join(unknown_attendee_emails)),
            )

        attendee_user_ids.update(user.id for user in attendee_users)

    for occurrence in occurrences:
        for attendee_user_id in attendee_user_ids:
            session.add(
                MeetingOccurrenceAttendee(
                    occurrence_id=occurrence.id,
                    user_id=attendee_user_id,
                )
            )

    await session.commit()

    log_with_fields(
        logger,
        logging.INFO,
        "series created",
        user_id=current_user.id,
        series_id=series.id,
        occurrence_count=len(occurrences),
        attendee_count=len(attendee_user_ids),
        reminder_minutes_before=series.reminder_minutes_before,
        recurrence_freq=recurrence_freq,
        recurrence_interval=recurrence_interval,
    )

    return RedirectResponse(url="/", status_code=303)


@router.get("/series/{series_id}", response_class=HTMLResponse, name="series_detail")
async def series_detail(
    request: Request,
    series_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> HTMLResponse:
    return await render_series_detail(
        request=request,
        session=session,
        series_id=series_id,
        current_user=current_user,
    )


@router.post("/series/{series_id}/attendees", response_class=RedirectResponse)
async def add_series_attendee(
    request: Request,
    series_id: uuid.UUID,
    email: str = Form(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> Response:
    await get_owned_series_or_404(session, series_id, current_user.id)

    normalized_email = email.strip().lower()
    attendee_form = {"email": normalized_email}
    attendee_form_errors: dict[str, str] = {}

    if not normalized_email:
        attendee_form_errors["email"] = "Attendee email is required."

    attendee_user: User | None = None
    if not attendee_form_errors:
        attendee_user = await resolve_series_attendee_user(session, normalized_email)
        if attendee_user is None:
            attendee_form_errors["email"] = "No user found with that email."

    if attendee_form_errors:
        return await render_series_detail(
            request=request,
            session=session,
            series_id=series_id,
            current_user=current_user,
            status_code=400,
            attendee_form=attendee_form,
            attendee_form_errors=attendee_form_errors,
        )

    if attendee_user is None:
        raise HTTPException(status_code=400, detail="Invalid attendee")

    occ_repo = MeetingOccurrenceRepository(session)
    occurrences = await occ_repo.list_for_series(series_id)
    occurrence_ids = [occ.id for occ in occurrences]

    existing_occurrence_ids = await existing_attendee_occurrence_ids(
        session=session,
        attendee_user_id=attendee_user.id,
        occurrence_ids=occurrence_ids,
    )
    added_count = add_missing_attendee_links(
        session=session,
        attendee_user_id=attendee_user.id,
        occurrence_ids=occurrence_ids,
        existing_occurrence_ids=existing_occurrence_ids,
    )

    if added_count > 0:
        await session.commit()
        log_with_fields(
            logger,
            logging.INFO,
            "series attendee added",
            user_id=current_user.id,
            series_id=series_id,
            attendee_user_id=attendee_user.id,
            attendee_email=attendee_user.email,
            occurrences_linked=added_count,
        )

    return RedirectResponse(
        url=request.app.url_path_for("series_detail", series_id=str(series_id)),
        status_code=303,
    )


@router.post("/series/{series_id}/occurrences", response_class=RedirectResponse)
async def create_occurrence(
    request: Request,
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

    return RedirectResponse(
        url=request.app.url_path_for("series_detail", series_id=str(series_id)),
        status_code=303,
    )

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import cast
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from authlib.integrations.starlette_client import OAuthError
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from agendable.auth import hash_password, require_user, verify_password
from agendable.db import get_session
from agendable.models import (
    AgendaItem,
    ExternalIdentity,
    MeetingOccurrence,
    MeetingSeries,
    Task,
    User,
)
from agendable.recurrence import build_rrule, describe_recurrence, generate_datetimes
from agendable.repos import (
    AgendaItemRepository,
    ExternalIdentityRepository,
    MeetingOccurrenceRepository,
    MeetingSeriesRepository,
    TaskRepository,
    UserRepository,
)
from agendable.settings import get_settings
from agendable.sso_google import build_oauth, google_enabled


def _parse_dt(value: str) -> datetime:
    # Expect HTML datetime-local (no timezone). Treat as UTC for now.
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid datetime") from exc

    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def _parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid date") from exc


def _parse_time(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid time") from exc


def _parse_timezone(value: str) -> ZoneInfo:
    name = value.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Invalid timezone")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=400, detail="Unknown timezone") from exc


router = APIRouter()

templates_dir = Path(__file__).resolve().parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

oauth = build_oauth()


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    current_user: User | None
    try:
        current_user = await require_user(request, session)
    except HTTPException:
        current_user = None

    series: list[MeetingSeries] = []
    series_recurrence: dict[uuid.UUID, str] = {}
    if current_user is not None:
        series_repo = MeetingSeriesRepository(session)
        series = await series_repo.list_for_owner(current_user.id)
        series_recurrence = {
            s.id: (
                describe_recurrence(
                    rrule=s.recurrence_rrule,
                    dtstart=s.recurrence_dtstart,
                    timezone=s.recurrence_timezone,
                )
                if s.recurrence_rrule
                else f"Every {s.default_interval_days} days"
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


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "error": None,
            "current_user": None,
            "google_enabled": google_enabled(),
        },
    )


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_session),
) -> Response:
    normalized_email = email.strip().lower()

    users = UserRepository(session)
    user = await users.get_by_email(normalized_email)

    if user is None:
        user = User(
            email=normalized_email,
            display_name=normalized_email,
            password_hash=hash_password(password),
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
    else:
        if user.password_hash is None or not verify_password(password, user.password_hash):
            return templates.TemplateResponse(
                request,
                "login.html",
                {
                    "error": "Invalid email or password",
                    "current_user": None,
                    "google_enabled": google_enabled(),
                },
                status_code=401,
            )

    request.session["user_id"] = str(user.id)
    return RedirectResponse(url="/", status_code=303)


@router.get("/auth/google/start", response_class=RedirectResponse)
async def google_start(request: Request) -> Response:
    if not google_enabled():
        raise HTTPException(status_code=404)

    redirect_uri = str(request.url_for("google_callback"))
    return cast(Response, await oauth.google.authorize_redirect(request, redirect_uri))


@router.get("/auth/google/callback", name="google_callback")
async def google_callback(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    if not google_enabled():
        raise HTTPException(status_code=404)

    try:
        token = await oauth.google.authorize_access_token(request)
        userinfo = await oauth.google.parse_id_token(request, token)
    except OAuthError:
        return RedirectResponse(url="/login", status_code=303)

    sub = str(userinfo.get("sub", ""))
    email = str(userinfo.get("email", "")).strip().lower()
    email_verified = bool(userinfo.get("email_verified"))
    name = str(userinfo.get("name") or email)

    if not sub or not email or not email_verified:
        return RedirectResponse(url="/login", status_code=303)

    settings = get_settings()
    if settings.allowed_email_domain is not None:
        allowed = settings.allowed_email_domain.strip().lower().lstrip("@")
        if not email.endswith(f"@{allowed}"):
            return templates.TemplateResponse(
                request,
                "login.html",
                {
                    "error": "Email domain not allowed",
                    "current_user": None,
                    "google_enabled": google_enabled(),
                },
                status_code=403,
            )

    ext_repo = ExternalIdentityRepository(session)
    ext = await ext_repo.get_by_provider_subject("google", sub)

    if ext is not None:
        users = UserRepository(session)
        user = await users.get_by_id(ext.user_id)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
    else:
        users = UserRepository(session)
        user = await users.get_by_email(email)
        if user is None:
            user = User(email=email, display_name=name, password_hash=None)
            session.add(user)
            await session.commit()
            await session.refresh(user)

        ext = ExternalIdentity(user_id=user.id, provider="google", subject=sub, email=email)
        session.add(ext)
        await session.commit()

    request.session["user_id"] = str(user.id)
    return RedirectResponse(url="/", status_code=303)


@router.post("/logout", response_class=RedirectResponse)
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


@router.post("/series", response_class=RedirectResponse)
async def create_series(
    title: str = Form(...),
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
    if generate_count < 1 or generate_count > 200:
        raise HTTPException(status_code=400, detail="generate_count must be between 1 and 200")

    if recurrence_interval < 1 or recurrence_interval > 365:
        raise HTTPException(status_code=400, detail="recurrence_interval must be between 1 and 365")

    start_date = _parse_date(recurrence_start_date)
    start_time = _parse_time(recurrence_time)
    tz = _parse_timezone(recurrence_timezone)
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

    series = MeetingSeries(
        owner_user_id=current_user.id,
        title=title,
        default_interval_days=7,
        recurrence_rrule=normalized_rrule,
        recurrence_dtstart=dtstart,
        recurrence_timezone=recurrence_timezone.strip(),
    )
    session.add(series)

    # Ensure `series.id` is available before creating occurrences.
    await session.flush()

    try:
        scheduled = generate_datetimes(
            rrule=normalized_rrule, dtstart=dtstart, count=generate_count
        )
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid recurrence") from exc

    if not scheduled:
        raise HTTPException(status_code=400, detail="RRULE produced no occurrences")

    session.add_all(
        [
            MeetingOccurrence(
                series_id=series.id,
                scheduled_at=dt.astimezone(UTC),
                notes="",
            )
            for dt in scheduled
        ]
    )

    await session.commit()

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

    tasks_repo = TaskRepository(session)
    tasks = await tasks_repo.list_for_series(series_id)

    # Load agenda for most recent occurrence (if any)
    agenda_items: list[AgendaItem] = []
    active_occurrence: MeetingOccurrence | None = occurrences[0] if occurrences else None
    if active_occurrence is not None:
        agenda_repo = AgendaItemRepository(session)
        agenda_items = await agenda_repo.list_for_occurrence(active_occurrence.id)

    return templates.TemplateResponse(
        request,
        "series_detail.html",
        {
            "series": series,
            "recurrence_label": (
                describe_recurrence(
                    rrule=series.recurrence_rrule,
                    dtstart=series.recurrence_dtstart,
                    timezone=series.recurrence_timezone,
                )
                if series.recurrence_rrule
                else f"Every {series.default_interval_days} days"
            ),
            "occurrences": occurrences,
            "active_occurrence": active_occurrence,
            "tasks": tasks,
            "agenda_items": agenda_items,
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
    if await series_repo.get_for_owner(series_id, current_user.id) is None:
        raise HTTPException(status_code=404)

    occ = MeetingOccurrence(series_id=series_id, scheduled_at=_parse_dt(scheduled_at), notes="")
    session.add(occ)
    await session.commit()

    return RedirectResponse(url=f"/series/{series_id}", status_code=303)


@router.post("/series/{series_id}/tasks", response_class=RedirectResponse)
async def create_task(
    series_id: uuid.UUID,
    title: str = Form(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> RedirectResponse:
    series_repo = MeetingSeriesRepository(session)
    if await series_repo.get_for_owner(series_id, current_user.id) is None:
        raise HTTPException(status_code=404)

    task = Task(series_id=series_id, title=title)
    session.add(task)
    await session.commit()
    return RedirectResponse(url=f"/series/{series_id}", status_code=303)


@router.post("/tasks/{task_id}/toggle", response_class=RedirectResponse)
async def toggle_task(
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> RedirectResponse:
    tasks_repo = TaskRepository(session)
    task = await tasks_repo.get_by_id(task_id)
    if task is None:
        raise HTTPException(status_code=404)

    series_repo = MeetingSeriesRepository(session)
    if await series_repo.get_for_owner(task.series_id, current_user.id) is None:
        raise HTTPException(status_code=404)

    task.is_done = not task.is_done
    await session.commit()
    return RedirectResponse(url=f"/series/{task.series_id}", status_code=303)


@router.post("/occurrences/{occurrence_id}/agenda", response_class=RedirectResponse)
async def add_agenda_item(
    occurrence_id: uuid.UUID,
    body: str = Form(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> RedirectResponse:
    occ_repo = MeetingOccurrenceRepository(session)
    occurrence = await occ_repo.get_by_id(occurrence_id)
    if occurrence is None:
        raise HTTPException(status_code=404)

    series_repo = MeetingSeriesRepository(session)
    if await series_repo.get_for_owner(occurrence.series_id, current_user.id) is None:
        raise HTTPException(status_code=404)

    item = AgendaItem(occurrence_id=occurrence_id, body=body)
    session.add(item)
    await session.commit()

    return RedirectResponse(url=f"/series/{occurrence.series_id}", status_code=303)


@router.post("/agenda/{item_id}/toggle", response_class=RedirectResponse)
async def toggle_agenda_item(
    item_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> RedirectResponse:
    agenda_repo = AgendaItemRepository(session)
    item = await agenda_repo.get_by_id(item_id)
    if item is None:
        raise HTTPException(status_code=404)

    item.is_done = not item.is_done

    occ_repo = MeetingOccurrenceRepository(session)
    occurrence = await occ_repo.get_by_id(item.occurrence_id)
    if occurrence is None:
        raise HTTPException(status_code=404)

    series_repo = MeetingSeriesRepository(session)
    if await series_repo.get_for_owner(occurrence.series_id, current_user.id) is None:
        raise HTTPException(status_code=404)

    await session.commit()
    return RedirectResponse(url=f"/series/{occurrence.series_id}", status_code=303)

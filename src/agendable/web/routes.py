from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

from authlib.integrations.starlette_client import OAuthError
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
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
    if current_user is not None:
        result = await session.execute(
            select(MeetingSeries)
            .where(MeetingSeries.owner_user_id == current_user.id)
            .order_by(MeetingSeries.created_at.desc())
        )
        series = list(result.scalars().all())

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "series": series,
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

    result = await session.execute(select(User).where(User.email == normalized_email))
    user = result.scalar_one_or_none()

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

    ext_result = await session.execute(
        select(ExternalIdentity).where(
            ExternalIdentity.provider == "google",
            ExternalIdentity.subject == sub,
        )
    )
    ext = ext_result.scalar_one_or_none()

    if ext is not None:
        user_result = await session.execute(select(User).where(User.id == ext.user_id))
        user = user_result.scalar_one_or_none()
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
    else:
        user_result = await session.execute(select(User).where(User.email == email))
        user = user_result.scalar_one_or_none()
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
    default_interval_days: int = Form(7),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> RedirectResponse:
    series = MeetingSeries(
        owner_user_id=current_user.id,
        title=title,
        default_interval_days=default_interval_days,
    )
    session.add(series)
    await session.commit()

    return RedirectResponse(url="/", status_code=303)


@router.get("/series/{series_id}", response_class=HTMLResponse)
async def series_detail(
    request: Request,
    series_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> HTMLResponse:
    series_result = await session.execute(
        select(MeetingSeries).where(
            MeetingSeries.id == series_id,
            MeetingSeries.owner_user_id == current_user.id,
        )
    )
    series = series_result.scalar_one_or_none()
    if series is None:
        raise HTTPException(status_code=404)

    occ_result = await session.execute(
        select(MeetingOccurrence)
        .where(MeetingOccurrence.series_id == series_id)
        .order_by(MeetingOccurrence.scheduled_at.desc())
    )
    occurrences = list(occ_result.scalars().all())

    tasks_result = await session.execute(
        select(Task).where(Task.series_id == series_id).order_by(Task.created_at.desc())
    )
    tasks = list(tasks_result.scalars().all())

    # Load agenda for most recent occurrence (if any)
    agenda_items: list[AgendaItem] = []
    active_occurrence: MeetingOccurrence | None = occurrences[0] if occurrences else None
    if active_occurrence is not None:
        agenda_result = await session.execute(
            select(AgendaItem)
            .where(AgendaItem.occurrence_id == active_occurrence.id)
            .order_by(AgendaItem.created_at.desc())
        )
        agenda_items = list(agenda_result.scalars().all())

    return templates.TemplateResponse(
        request,
        "series_detail.html",
        {
            "series": series,
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
    series_result = await session.execute(
        select(MeetingSeries).where(
            MeetingSeries.id == series_id,
            MeetingSeries.owner_user_id == current_user.id,
        )
    )
    if series_result.scalar_one_or_none() is None:
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
    series_result = await session.execute(
        select(MeetingSeries).where(
            MeetingSeries.id == series_id,
            MeetingSeries.owner_user_id == current_user.id,
        )
    )
    if series_result.scalar_one_or_none() is None:
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
    result = await session.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404)

    series_result = await session.execute(
        select(MeetingSeries).where(MeetingSeries.id == task.series_id)
    )
    series = series_result.scalar_one_or_none()
    if series is None or series.owner_user_id != current_user.id:
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
    occ_result = await session.execute(
        select(MeetingOccurrence).where(MeetingOccurrence.id == occurrence_id)
    )
    occurrence = occ_result.scalar_one_or_none()
    if occurrence is None:
        raise HTTPException(status_code=404)

    series_result = await session.execute(
        select(MeetingSeries).where(
            MeetingSeries.id == occurrence.series_id,
            MeetingSeries.owner_user_id == current_user.id,
        )
    )
    if series_result.scalar_one_or_none() is None:
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
    item_result = await session.execute(select(AgendaItem).where(AgendaItem.id == item_id))
    item = item_result.scalar_one_or_none()
    if item is None:
        raise HTTPException(status_code=404)

    item.is_done = not item.is_done

    occ_result = await session.execute(
        select(MeetingOccurrence).where(MeetingOccurrence.id == item.occurrence_id)
    )
    occurrence = occ_result.scalar_one()

    series_result = await session.execute(
        select(MeetingSeries).where(MeetingSeries.id == occurrence.series_id)
    )
    series = series_result.scalar_one_or_none()
    if series is None or series.owner_user_id != current_user.id:
        raise HTTPException(status_code=404)

    await session.commit()
    return RedirectResponse(url=f"/series/{occurrence.series_id}", status_code=303)

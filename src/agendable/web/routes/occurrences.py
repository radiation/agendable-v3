from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from starlette.responses import Response

from agendable.auth import require_user
from agendable.db import get_session
from agendable.db.models import (
    AgendaItem,
    MeetingOccurrence,
    MeetingOccurrenceAttendee,
    MeetingSeries,
    Task,
    User,
)
from agendable.db.repos import (
    AgendaItemRepository,
    MeetingOccurrenceRepository,
    MeetingSeriesRepository,
    TaskRepository,
    UserRepository,
)
from agendable.logging_config import log_with_fields
from agendable.services import complete_occurrence_and_roll_forward
from agendable.web.routes.common import (
    format_datetime_local_value,
    parse_dt_for_timezone,
    recurrence_label,
    templates,
)

router = APIRouter()
logger = logging.getLogger("agendable.occurrences")

_PRESENCE_WINDOW_SECONDS = 30
_occurrence_presence: dict[uuid.UUID, dict[uuid.UUID, datetime]] = {}
_occurrence_last_activity: dict[uuid.UUID, tuple[datetime, str]] = {}


def _base_task_form(
    *,
    task_due_default_value: str,
) -> dict[str, str]:
    return {
        "title": "",
        "description": "",
        "assigned_user_id": "",
        "due_at": task_due_default_value,
    }


def _base_agenda_form() -> dict[str, str]:
    return {
        "body": "",
        "description": "",
    }


def _base_attendee_form() -> dict[str, str]:
    return {"email": ""}


def _ensure_occurrence_writable(occurrence_id: uuid.UUID, is_completed: bool) -> None:
    if is_completed:
        raise HTTPException(
            status_code=400,
            detail=f"Meeting {occurrence_id} is completed and read-only",
        )


def _normalize_optional_text(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        return None
    return normalized


def _relative_time_label(*, then: datetime, now: datetime) -> str:
    normalized_then = _as_utc(then)
    normalized_now = _as_utc(now)
    delta_seconds = max(0, int((normalized_now - normalized_then).total_seconds()))
    if delta_seconds < 5:
        return "just now"
    if delta_seconds < 60:
        return f"{delta_seconds}s ago"
    if delta_seconds < 3600:
        return f"{delta_seconds // 60}m ago"
    return f"{delta_seconds // 3600}h ago"


def _mark_presence(*, occurrence_id: uuid.UUID, user_id: uuid.UUID, now: datetime) -> int:
    occurrence_presence = _occurrence_presence.setdefault(occurrence_id, {})
    cutoff = now.timestamp() - _PRESENCE_WINDOW_SECONDS
    stale_user_ids = [
        seen_user_id
        for seen_user_id, seen_at in occurrence_presence.items()
        if seen_at.timestamp() < cutoff
    ]
    for stale_user_id in stale_user_ids:
        occurrence_presence.pop(stale_user_id, None)
    occurrence_presence[user_id] = now
    return len(occurrence_presence)


def _record_occurrence_activity(
    *,
    occurrence_id: uuid.UUID,
    actor_display_name: str,
    now: datetime,
) -> None:
    _occurrence_last_activity[occurrence_id] = (_as_utc(now), actor_display_name)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _latest_content_activity_at(
    *,
    occurrence: MeetingOccurrence,
    tasks: list[Task],
    agenda_items: list[AgendaItem],
) -> datetime:
    latest = _as_utc(occurrence.created_at)
    for task in tasks:
        task_created_at = _as_utc(task.created_at)
        if task_created_at > latest:
            latest = task_created_at
    for agenda_item in agenda_items:
        agenda_created_at = _as_utc(agenda_item.created_at)
        if agenda_created_at > latest:
            latest = agenda_created_at
    return latest


async def _get_owned_occurrence(
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


async def _get_accessible_occurrence(
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


async def _get_default_task_due_at(
    session: AsyncSession,
    occurrence: MeetingOccurrence,
) -> datetime:
    occ_repo = MeetingOccurrenceRepository(session)
    next_occurrence = await occ_repo.get_next_for_series(
        occurrence.series_id, occurrence.scheduled_at
    )
    if next_occurrence is not None:
        return next_occurrence.scheduled_at
    return occurrence.scheduled_at


async def _task_due_default_value(
    session: AsyncSession,
    occurrence: MeetingOccurrence,
    timezone: str,
) -> str:
    due_at = await _get_default_task_due_at(session, occurrence)
    return format_datetime_local_value(due_at, timezone)


async def _list_occurrence_attendee_users(
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


async def _occurrence_collections(
    session: AsyncSession,
    occurrence: MeetingOccurrence,
    current_user: User,
) -> tuple[list[Task], list[AgendaItem], list[User]]:
    tasks_repo = TaskRepository(session)
    tasks = await tasks_repo.list_for_occurrence(occurrence.id)

    agenda_repo = AgendaItemRepository(session)
    agenda_items = await agenda_repo.list_for_occurrence(occurrence.id)

    attendee_users = await _list_occurrence_attendee_users(session, occurrence.id, current_user)
    return tasks, agenda_items, attendee_users


async def _shared_panel_context(
    *,
    session: AsyncSession,
    occurrence: MeetingOccurrence,
    current_user: User,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    active_viewers_count = _mark_presence(
        occurrence_id=occurrence.id,
        user_id=current_user.id,
        now=now,
    )
    tasks, agenda_items, attendee_users = await _occurrence_collections(
        session,
        occurrence,
        current_user,
    )

    latest_content_activity_at = _latest_content_activity_at(
        occurrence=occurrence,
        tasks=tasks,
        agenda_items=agenda_items,
    )
    latest_activity_at = latest_content_activity_at
    latest_activity_actor: str | None = None
    tracked_activity = _occurrence_last_activity.get(occurrence.id)
    if tracked_activity is not None:
        tracked_activity_at, tracked_activity_actor = tracked_activity
        if tracked_activity_at >= latest_activity_at:
            latest_activity_at = tracked_activity_at
            latest_activity_actor = tracked_activity_actor

    latest_activity_text = _relative_time_label(then=latest_activity_at, now=now)
    if latest_activity_actor is not None:
        latest_activity_text = f"{latest_activity_text} by {latest_activity_actor}"

    return {
        "occurrence": occurrence,
        "tasks": tasks,
        "agenda_items": agenda_items,
        "attendee_users": attendee_users,
        "presence_window_seconds": _PRESENCE_WINDOW_SECONDS,
        "active_viewers_count": active_viewers_count,
        "latest_activity_text": latest_activity_text,
        "current_user": current_user,
        "refreshed_at": now,
    }


def _merged_task_form(
    *,
    task_due_default_value: str,
    task_form: dict[str, str] | None,
    current_user: User,
) -> dict[str, str]:
    selected_task_form = _base_task_form(task_due_default_value=task_due_default_value)
    if task_form is not None:
        selected_task_form.update(task_form)
    if not selected_task_form.get("assigned_user_id"):
        selected_task_form["assigned_user_id"] = str(current_user.id)
    return selected_task_form


def _merged_form(
    *,
    base: dict[str, str],
    form: dict[str, str] | None,
) -> dict[str, str]:
    selected = dict(base)
    if form is not None:
        selected.update(form)
    return selected


async def _resolve_task_due_at(
    *,
    session: AsyncSession,
    occurrence: MeetingOccurrence,
    due_at_input: str | None,
    timezone: str,
    task_form_errors: dict[str, str],
) -> datetime:
    final_due_at = await _get_default_task_due_at(session, occurrence)
    if due_at_input is None or not due_at_input.strip():
        return final_due_at

    try:
        return parse_dt_for_timezone(due_at_input, timezone)
    except HTTPException:
        task_form_errors["due_at"] = "Enter a valid due date and time."
        return final_due_at


async def _validate_task_assignee(
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


async def _occurrence_detail_context(
    *,
    session: AsyncSession,
    occurrence: MeetingOccurrence,
    series: MeetingSeries,
    current_user: User,
    task_form: dict[str, str] | None = None,
    task_form_errors: dict[str, str] | None = None,
    agenda_form: dict[str, str] | None = None,
    agenda_form_errors: dict[str, str] | None = None,
    attendee_form: dict[str, str] | None = None,
    attendee_form_errors: dict[str, str] | None = None,
) -> dict[str, Any]:
    task_due_default_value = await _task_due_default_value(
        session,
        occurrence,
        current_user.timezone,
    )

    tasks, agenda_items, attendee_users = await _occurrence_collections(
        session,
        occurrence,
        current_user,
    )

    selected_task_form = _merged_task_form(
        task_due_default_value=task_due_default_value,
        task_form=task_form,
        current_user=current_user,
    )
    selected_agenda_form = _merged_form(base=_base_agenda_form(), form=agenda_form)
    selected_attendee_form = _merged_form(base=_base_attendee_form(), form=attendee_form)

    return {
        "series": series,
        "recurrence_label": recurrence_label(
            recurrence_rrule=series.recurrence_rrule,
            recurrence_dtstart=series.recurrence_dtstart,
            recurrence_timezone=series.recurrence_timezone,
            default_interval_days=series.default_interval_days,
        ),
        "occurrence": occurrence,
        "tasks": tasks,
        "task_due_default_value": task_due_default_value,
        "task_form": selected_task_form,
        "task_form_errors": task_form_errors or {},
        "agenda_items": agenda_items,
        "agenda_form": selected_agenda_form,
        "agenda_form_errors": agenda_form_errors or {},
        "attendee_form": selected_attendee_form,
        "attendee_form_errors": attendee_form_errors or {},
        "attendee_users": attendee_users,
        "current_user": current_user,
        "refreshed_at": datetime.now(UTC),
    }


async def _render_occurrence_detail(
    *,
    request: Request,
    session: AsyncSession,
    occurrence: MeetingOccurrence,
    series: MeetingSeries,
    current_user: User,
    status_code: int = 200,
    task_form: dict[str, str] | None = None,
    task_form_errors: dict[str, str] | None = None,
    agenda_form: dict[str, str] | None = None,
    agenda_form_errors: dict[str, str] | None = None,
    attendee_form: dict[str, str] | None = None,
    attendee_form_errors: dict[str, str] | None = None,
) -> HTMLResponse:
    context = await _occurrence_detail_context(
        session=session,
        occurrence=occurrence,
        series=series,
        current_user=current_user,
        task_form=task_form,
        task_form_errors=task_form_errors,
        agenda_form=agenda_form,
        agenda_form_errors=agenda_form_errors,
        attendee_form=attendee_form,
        attendee_form_errors=attendee_form_errors,
    )
    return templates.TemplateResponse(
        request,
        "occurrence_detail.html",
        context,
        status_code=status_code,
    )


@router.get("/occurrences/{occurrence_id}", response_class=HTMLResponse, name="occurrence_detail")
async def occurrence_detail(
    request: Request,
    occurrence_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> HTMLResponse:
    occurrence, series = await _get_accessible_occurrence(session, occurrence_id, current_user.id)
    _mark_presence(occurrence_id=occurrence.id, user_id=current_user.id, now=datetime.now(UTC))
    return await _render_occurrence_detail(
        request=request,
        session=session,
        occurrence=occurrence,
        series=series,
        current_user=current_user,
    )


@router.get(
    "/occurrences/{occurrence_id}/shared-panel",
    response_class=HTMLResponse,
    name="occurrence_shared_panel",
)
async def occurrence_shared_panel(
    request: Request,
    occurrence_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> HTMLResponse:
    occurrence, _ = await _get_accessible_occurrence(session, occurrence_id, current_user.id)
    context = await _shared_panel_context(
        session=session,
        occurrence=occurrence,
        current_user=current_user,
    )
    return templates.TemplateResponse(
        request,
        "partials/occurrence_shared_panel.html",
        context,
    )


@router.post("/occurrences/{occurrence_id}/tasks", response_class=RedirectResponse)
async def create_task(
    request: Request,
    occurrence_id: uuid.UUID,
    title: str = Form(...),
    description_input: str | None = Form(None, alias="description"),
    due_at_input: str | None = Form(None, alias="due_at"),
    assigned_user_id: uuid.UUID | None = Form(None),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> Response:
    occurrence, series = await _get_accessible_occurrence(session, occurrence_id, current_user.id)

    _ensure_occurrence_writable(occurrence.id, occurrence.is_completed)

    normalized_title = title.strip()
    task_form_errors: dict[str, str] = {}
    task_form = {
        "title": normalized_title,
        "description": description_input or "",
        "assigned_user_id": str(assigned_user_id) if assigned_user_id is not None else "",
        "due_at": due_at_input or "",
    }
    if not normalized_title:
        task_form_errors["title"] = "Task title is required."

    final_due_at = await _resolve_task_due_at(
        session=session,
        occurrence=occurrence,
        due_at_input=due_at_input,
        timezone=current_user.timezone,
        task_form_errors=task_form_errors,
    )

    final_assignee_id = assigned_user_id or current_user.id
    normalized_description = _normalize_optional_text(description_input)
    await _validate_task_assignee(
        session=session,
        occurrence_id=occurrence_id,
        series_owner_user_id=series.owner_user_id,
        assignee_id=final_assignee_id,
        task_form_errors=task_form_errors,
    )

    if task_form_errors:
        return await _render_occurrence_detail(
            request=request,
            session=session,
            occurrence=occurrence,
            series=series,
            current_user=current_user,
            status_code=400,
            task_form=task_form,
            task_form_errors=task_form_errors,
        )

    task = Task(
        occurrence_id=occurrence_id,
        title=normalized_title,
        description=normalized_description,
        assigned_user_id=final_assignee_id,
        due_at=final_due_at,
    )
    session.add(task)
    await session.commit()
    _record_occurrence_activity(
        occurrence_id=occurrence.id,
        actor_display_name=current_user.full_name,
        now=datetime.now(UTC),
    )

    log_with_fields(
        logger,
        logging.INFO,
        "task created",
        user_id=current_user.id,
        occurrence_id=occurrence_id,
        task_id=task.id,
        assigned_user_id=final_assignee_id,
        due_at=final_due_at,
    )
    return RedirectResponse(
        url=request.app.url_path_for("occurrence_detail", occurrence_id=str(occurrence_id)),
        status_code=303,
    )


@router.post("/occurrences/{occurrence_id}/attendees", response_class=RedirectResponse)
async def add_attendee(
    request: Request,
    occurrence_id: uuid.UUID,
    email: str = Form(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> Response:
    occurrence, series = await _get_owned_occurrence(session, occurrence_id, current_user.id)

    _ensure_occurrence_writable(occurrence.id, occurrence.is_completed)

    normalized_email = email.strip().lower()
    attendee_form_errors: dict[str, str] = {}
    attendee_form = {"email": normalized_email}

    if not normalized_email:
        attendee_form_errors["email"] = "Attendee email is required."

    attendee_user: User | None = None
    if not attendee_form_errors:
        users_repo = UserRepository(session)
        attendee_user = await users_repo.get_by_email(normalized_email)
        if attendee_user is None:
            attendee_form_errors["email"] = "No user found with that email."

    if attendee_form_errors:
        return await _render_occurrence_detail(
            request=request,
            session=session,
            occurrence=occurrence,
            series=series,
            current_user=current_user,
            status_code=400,
            attendee_form=attendee_form,
            attendee_form_errors=attendee_form_errors,
        )

    if attendee_user is None:
        raise HTTPException(status_code=400, detail="Invalid attendee")

    existing = (
        await session.execute(
            select(MeetingOccurrenceAttendee).where(
                MeetingOccurrenceAttendee.occurrence_id == occurrence_id,
                MeetingOccurrenceAttendee.user_id == attendee_user.id,
            )
        )
    ).scalar_one_or_none()
    if existing is None:
        session.add(
            MeetingOccurrenceAttendee(occurrence_id=occurrence_id, user_id=attendee_user.id)
        )
        await session.commit()
        _record_occurrence_activity(
            occurrence_id=occurrence.id,
            actor_display_name=current_user.full_name,
            now=datetime.now(UTC),
        )
        log_with_fields(
            logger,
            logging.INFO,
            "attendee added",
            user_id=current_user.id,
            occurrence_id=occurrence_id,
            attendee_user_id=attendee_user.id,
            attendee_email=attendee_user.email,
        )

    return RedirectResponse(
        url=request.app.url_path_for("occurrence_detail", occurrence_id=str(occurrence_id)),
        status_code=303,
    )


@router.post("/tasks/{task_id}/toggle", response_class=RedirectResponse)
async def toggle_task(
    request: Request,
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> RedirectResponse:
    tasks_repo = TaskRepository(session)
    task = await tasks_repo.get_by_id(task_id)
    if task is None:
        raise HTTPException(status_code=404)

    occ_repo = MeetingOccurrenceRepository(session)
    occurrence = await occ_repo.get_by_id(task.occurrence_id)
    if occurrence is None:
        raise HTTPException(status_code=404)

    await _get_accessible_occurrence(session, occurrence.id, current_user.id)

    _ensure_occurrence_writable(occurrence.id, occurrence.is_completed)

    task.is_done = not task.is_done
    await session.commit()
    _record_occurrence_activity(
        occurrence_id=occurrence.id,
        actor_display_name=current_user.full_name,
        now=datetime.now(UTC),
    )

    log_with_fields(
        logger,
        logging.INFO,
        "task toggled",
        user_id=current_user.id,
        occurrence_id=occurrence.id,
        task_id=task.id,
        is_done=task.is_done,
    )
    return RedirectResponse(
        url=request.app.url_path_for("occurrence_detail", occurrence_id=str(occurrence.id)),
        status_code=303,
    )


@router.post("/occurrences/{occurrence_id}/agenda", response_class=RedirectResponse)
async def add_agenda_item(
    request: Request,
    occurrence_id: uuid.UUID,
    body: str = Form(...),
    description_input: str | None = Form(None, alias="description"),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> Response:
    occurrence, series = await _get_accessible_occurrence(session, occurrence_id, current_user.id)

    _ensure_occurrence_writable(occurrence.id, occurrence.is_completed)

    normalized_body = body.strip()
    normalized_description = _normalize_optional_text(description_input)
    if not normalized_body:
        return await _render_occurrence_detail(
            request=request,
            session=session,
            occurrence=occurrence,
            series=series,
            current_user=current_user,
            status_code=400,
            agenda_form={"body": body, "description": description_input or ""},
            agenda_form_errors={"body": "Agenda item is required."},
        )

    item = AgendaItem(
        occurrence_id=occurrence_id,
        body=normalized_body,
        description=normalized_description,
    )
    session.add(item)
    await session.commit()
    _record_occurrence_activity(
        occurrence_id=occurrence.id,
        actor_display_name=current_user.full_name,
        now=datetime.now(UTC),
    )

    log_with_fields(
        logger,
        logging.INFO,
        "agenda item created",
        user_id=current_user.id,
        occurrence_id=occurrence_id,
        agenda_item_id=item.id,
    )

    return RedirectResponse(
        url=request.app.url_path_for("occurrence_detail", occurrence_id=str(occurrence_id)),
        status_code=303,
    )


@router.post("/agenda/{item_id}/convert-to-task", response_class=RedirectResponse)
async def convert_agenda_item_to_task(
    request: Request,
    item_id: uuid.UUID,
    assigned_user_id: uuid.UUID = Form(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> RedirectResponse:
    agenda_repo = AgendaItemRepository(session)
    item = await agenda_repo.get_by_id(item_id)
    if item is None:
        raise HTTPException(status_code=404)

    occ_repo = MeetingOccurrenceRepository(session)
    occurrence = await occ_repo.get_by_id(item.occurrence_id)
    if occurrence is None:
        raise HTTPException(status_code=404)

    _, series = await _get_accessible_occurrence(session, occurrence.id, current_user.id)
    _ensure_occurrence_writable(occurrence.id, occurrence.is_completed)

    assignee_errors: dict[str, str] = {}
    await _validate_task_assignee(
        session=session,
        occurrence_id=occurrence.id,
        series_owner_user_id=series.owner_user_id,
        assignee_id=assigned_user_id,
        task_form_errors=assignee_errors,
    )
    if assignee_errors:
        raise HTTPException(status_code=400, detail=assignee_errors["assigned_user_id"])

    due_at = await _get_default_task_due_at(session, occurrence)
    title = item.body.strip() if item.body.strip() else "Agenda follow-up"
    task = Task(
        occurrence_id=occurrence.id,
        title=title,
        description=item.description,
        assigned_user_id=assigned_user_id,
        due_at=due_at,
    )
    item.is_done = True
    session.add(task)
    await session.commit()
    _record_occurrence_activity(
        occurrence_id=occurrence.id,
        actor_display_name=current_user.full_name,
        now=datetime.now(UTC),
    )

    log_with_fields(
        logger,
        logging.INFO,
        "agenda item converted to task",
        user_id=current_user.id,
        occurrence_id=occurrence.id,
        agenda_item_id=item.id,
        task_id=task.id,
        assigned_user_id=assigned_user_id,
    )

    return RedirectResponse(
        url=request.app.url_path_for("occurrence_detail", occurrence_id=str(occurrence.id)),
        status_code=303,
    )


@router.post("/agenda/{item_id}/toggle", response_class=RedirectResponse)
async def toggle_agenda_item(
    request: Request,
    item_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> RedirectResponse:
    agenda_repo = AgendaItemRepository(session)
    item = await agenda_repo.get_by_id(item_id)
    if item is None:
        raise HTTPException(status_code=404)

    occ_repo = MeetingOccurrenceRepository(session)
    occurrence = await occ_repo.get_by_id(item.occurrence_id)
    if occurrence is None:
        raise HTTPException(status_code=404)

    await _get_accessible_occurrence(session, occurrence.id, current_user.id)

    _ensure_occurrence_writable(occurrence.id, occurrence.is_completed)

    item.is_done = not item.is_done

    await session.commit()
    _record_occurrence_activity(
        occurrence_id=occurrence.id,
        actor_display_name=current_user.full_name,
        now=datetime.now(UTC),
    )

    log_with_fields(
        logger,
        logging.INFO,
        "agenda item toggled",
        user_id=current_user.id,
        occurrence_id=occurrence.id,
        agenda_item_id=item.id,
        is_done=item.is_done,
    )
    return RedirectResponse(
        url=request.app.url_path_for("occurrence_detail", occurrence_id=str(occurrence.id)),
        status_code=303,
    )


@router.post("/occurrences/{occurrence_id}/complete", response_class=RedirectResponse)
async def complete_occurrence(
    request: Request,
    occurrence_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> RedirectResponse:
    occurrence, _ = await _get_owned_occurrence(session, occurrence_id, current_user.id)

    if occurrence.is_completed:
        log_with_fields(
            logger,
            logging.INFO,
            "occurrence already completed",
            user_id=current_user.id,
            occurrence_id=occurrence.id,
        )
        return RedirectResponse(
            url=request.app.url_path_for("occurrence_detail", occurrence_id=str(occurrence.id)),
            status_code=303,
        )

    next_occurrence = await complete_occurrence_and_roll_forward(
        session,
        occurrence=occurrence,
    )
    _record_occurrence_activity(
        occurrence_id=occurrence.id,
        actor_display_name=current_user.full_name,
        now=datetime.now(UTC),
    )

    log_with_fields(
        logger,
        logging.INFO,
        "occurrence completed",
        user_id=current_user.id,
        occurrence_id=occurrence.id,
        next_occurrence_id=(next_occurrence.id if next_occurrence is not None else None),
    )

    redirect_occurrence_id = next_occurrence.id if next_occurrence is not None else occurrence.id
    return RedirectResponse(
        url=request.app.url_path_for(
            "occurrence_detail", occurrence_id=str(redirect_occurrence_id)
        ),
        status_code=303,
    )

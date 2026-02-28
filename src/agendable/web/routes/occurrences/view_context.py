from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db.models import AgendaItem, MeetingOccurrence, MeetingSeries, Task, User
from agendable.db.repos import AgendaItemRepository, MeetingOccurrenceRepository, TaskRepository
from agendable.web.routes.common import (
    format_datetime_local_value,
    parse_dt_for_timezone,
    recurrence_label,
    templates,
)
from agendable.web.routes.occurrences.access import list_occurrence_attendee_users
from agendable.web.routes.occurrences.collab import (
    PRESENCE_WINDOW_SECONDS,
    get_tracked_occurrence_activity,
    latest_content_activity_at,
    mark_presence,
    relative_time_label,
)


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


async def get_default_task_due_at(
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


async def task_due_default_value(
    session: AsyncSession,
    occurrence: MeetingOccurrence,
    timezone: str,
) -> str:
    due_at = await get_default_task_due_at(session, occurrence)
    return format_datetime_local_value(due_at, timezone)


async def occurrence_collections(
    session: AsyncSession,
    occurrence: MeetingOccurrence,
    current_user: User,
) -> tuple[list[Task], list[AgendaItem], list[User]]:
    tasks_repo = TaskRepository(session)
    tasks = await tasks_repo.list_for_occurrence(occurrence.id)

    agenda_repo = AgendaItemRepository(session)
    agenda_items = await agenda_repo.list_for_occurrence(occurrence.id)

    attendee_users = await list_occurrence_attendee_users(session, occurrence.id, current_user)
    return tasks, agenda_items, attendee_users


async def shared_panel_context(
    *,
    session: AsyncSession,
    occurrence: MeetingOccurrence,
    current_user: User,
) -> dict[str, Any]:
    now = datetime.now(UTC)
    active_viewers_count = mark_presence(
        occurrence_id=occurrence.id,
        user_id=current_user.id,
        now=now,
    )
    tasks, agenda_items, attendee_users = await occurrence_collections(
        session,
        occurrence,
        current_user,
    )

    latest_content_activity_at_value = latest_content_activity_at(
        occurrence=occurrence,
        tasks=tasks,
        agenda_items=agenda_items,
    )
    latest_activity_at = latest_content_activity_at_value
    latest_activity_actor: str | None = None
    tracked_activity = get_tracked_occurrence_activity(occurrence.id)
    if tracked_activity is not None:
        tracked_activity_at, tracked_activity_actor = tracked_activity
        if tracked_activity_at >= latest_activity_at:
            latest_activity_at = tracked_activity_at
            latest_activity_actor = tracked_activity_actor

    latest_activity_text = relative_time_label(then=latest_activity_at, now=now)
    if latest_activity_actor is not None:
        latest_activity_text = f"{latest_activity_text} by {latest_activity_actor}"

    return {
        "occurrence": occurrence,
        "tasks": tasks,
        "agenda_items": agenda_items,
        "attendee_users": attendee_users,
        "presence_window_seconds": PRESENCE_WINDOW_SECONDS,
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


async def resolve_task_due_at(
    *,
    session: AsyncSession,
    occurrence: MeetingOccurrence,
    due_at_input: str | None,
    timezone: str,
    task_form_errors: dict[str, str],
) -> datetime:
    final_due_at = await get_default_task_due_at(session, occurrence)
    if due_at_input is None or not due_at_input.strip():
        return final_due_at

    try:
        return parse_dt_for_timezone(due_at_input, timezone)
    except HTTPException:
        task_form_errors["due_at"] = "Enter a valid due date and time."
        return final_due_at


async def occurrence_detail_context(
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
    task_due_default = await task_due_default_value(
        session,
        occurrence,
        current_user.timezone,
    )

    tasks, agenda_items, attendee_users = await occurrence_collections(
        session,
        occurrence,
        current_user,
    )

    selected_task_form = _merged_task_form(
        task_due_default_value=task_due_default,
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
        "task_due_default_value": task_due_default,
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


async def render_occurrence_detail(
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
    context = await occurrence_detail_context(
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

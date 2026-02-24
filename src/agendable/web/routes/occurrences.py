from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

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


def _ensure_occurrence_writable(occurrence_id: uuid.UUID, is_completed: bool) -> None:
    if is_completed:
        raise HTTPException(
            status_code=400,
            detail=f"Meeting {occurrence_id} is completed and read-only",
        )


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


async def _ensure_occurrence_owner(
    session: AsyncSession,
    occurrence: MeetingOccurrence,
    owner_user_id: uuid.UUID,
) -> None:
    series_repo = MeetingSeriesRepository(session)
    if await series_repo.get_for_owner(occurrence.series_id, owner_user_id) is None:
        raise HTTPException(status_code=404)


@router.get("/occurrences/{occurrence_id}", response_class=HTMLResponse)
async def occurrence_detail(
    request: Request,
    occurrence_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> HTMLResponse:
    occurrence, series = await _get_owned_occurrence(session, occurrence_id, current_user.id)
    occ_repo = MeetingOccurrenceRepository(session)
    next_occurrence = await occ_repo.get_next_for_series(
        occurrence.series_id, occurrence.scheduled_at
    )
    task_due_default = (
        next_occurrence.scheduled_at if next_occurrence is not None else occurrence.scheduled_at
    )
    task_due_default_value = format_datetime_local_value(task_due_default, current_user.timezone)

    tasks_repo = TaskRepository(session)
    tasks = await tasks_repo.list_for_occurrence(occurrence.id)

    agenda_repo = AgendaItemRepository(session)
    agenda_items = await agenda_repo.list_for_occurrence(occurrence.id)

    attendee_links = list(
        (
            await session.execute(
                select(MeetingOccurrenceAttendee)
                .options(selectinload(MeetingOccurrenceAttendee.user))
                .where(MeetingOccurrenceAttendee.occurrence_id == occurrence.id)
            )
        )
        .scalars()
        .all()
    )
    attendee_users = [current_user]
    attendee_user_ids: set[uuid.UUID] = {current_user.id}
    for link in attendee_links:
        if link.user_id not in attendee_user_ids:
            attendee_users.append(link.user)
            attendee_user_ids.add(link.user_id)

    return templates.TemplateResponse(
        request,
        "occurrence_detail.html",
        {
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
            "agenda_items": agenda_items,
            "attendee_users": attendee_users,
            "current_user": current_user,
        },
    )


@router.post("/occurrences/{occurrence_id}/tasks", response_class=RedirectResponse)
async def create_task(
    occurrence_id: uuid.UUID,
    title: str = Form(...),
    due_at_input: str | None = Form(None, alias="due_at"),
    assigned_user_id: uuid.UUID | None = Form(None),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> RedirectResponse:
    occurrence, series = await _get_owned_occurrence(session, occurrence_id, current_user.id)

    _ensure_occurrence_writable(occurrence.id, occurrence.is_completed)

    occ_repo = MeetingOccurrenceRepository(session)
    next_occurrence = await occ_repo.get_next_for_series(
        occurrence.series_id, occurrence.scheduled_at
    )
    default_due_at = (
        next_occurrence.scheduled_at if next_occurrence is not None else occurrence.scheduled_at
    )
    final_due_at = (
        parse_dt_for_timezone(due_at_input, current_user.timezone)
        if due_at_input is not None and due_at_input.strip()
        else default_due_at
    )

    final_assignee_id = assigned_user_id or current_user.id
    users_repo = UserRepository(session)
    assignee = await users_repo.get_by_id(final_assignee_id)
    if assignee is None:
        raise HTTPException(status_code=400, detail="Invalid assignee")

    if final_assignee_id != series.owner_user_id:
        attendee_link = (
            await session.execute(
                select(MeetingOccurrenceAttendee).where(
                    MeetingOccurrenceAttendee.occurrence_id == occurrence_id,
                    MeetingOccurrenceAttendee.user_id == final_assignee_id,
                )
            )
        ).scalar_one_or_none()
        if attendee_link is None:
            raise HTTPException(status_code=400, detail="Assignee must be a meeting attendee")

    task = Task(
        occurrence_id=occurrence_id,
        title=title,
        assigned_user_id=final_assignee_id,
        due_at=final_due_at,
    )
    session.add(task)
    await session.commit()

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
    return RedirectResponse(url=f"/occurrences/{occurrence_id}", status_code=303)


@router.post("/occurrences/{occurrence_id}/attendees", response_class=RedirectResponse)
async def add_attendee(
    occurrence_id: uuid.UUID,
    email: str = Form(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> RedirectResponse:
    occurrence, _ = await _get_owned_occurrence(session, occurrence_id, current_user.id)

    _ensure_occurrence_writable(occurrence.id, occurrence.is_completed)

    users_repo = UserRepository(session)
    attendee_user = await users_repo.get_by_email(email)
    if attendee_user is None:
        raise HTTPException(status_code=400, detail="User not found")

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
        log_with_fields(
            logger,
            logging.INFO,
            "attendee added",
            user_id=current_user.id,
            occurrence_id=occurrence_id,
            attendee_user_id=attendee_user.id,
            attendee_email=attendee_user.email,
        )

    return RedirectResponse(url=f"/occurrences/{occurrence_id}", status_code=303)


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

    occ_repo = MeetingOccurrenceRepository(session)
    occurrence = await occ_repo.get_by_id(task.occurrence_id)
    if occurrence is None:
        raise HTTPException(status_code=404)

    await _ensure_occurrence_owner(session, occurrence, current_user.id)

    _ensure_occurrence_writable(occurrence.id, occurrence.is_completed)

    task.is_done = not task.is_done
    await session.commit()

    log_with_fields(
        logger,
        logging.INFO,
        "task toggled",
        user_id=current_user.id,
        occurrence_id=occurrence.id,
        task_id=task.id,
        is_done=task.is_done,
    )
    return RedirectResponse(url=f"/occurrences/{occurrence.id}", status_code=303)


@router.post("/occurrences/{occurrence_id}/agenda", response_class=RedirectResponse)
async def add_agenda_item(
    occurrence_id: uuid.UUID,
    body: str = Form(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> RedirectResponse:
    occurrence, _ = await _get_owned_occurrence(session, occurrence_id, current_user.id)

    _ensure_occurrence_writable(occurrence.id, occurrence.is_completed)

    item = AgendaItem(occurrence_id=occurrence_id, body=body)
    session.add(item)
    await session.commit()

    log_with_fields(
        logger,
        logging.INFO,
        "agenda item created",
        user_id=current_user.id,
        occurrence_id=occurrence_id,
        agenda_item_id=item.id,
    )

    return RedirectResponse(url=f"/occurrences/{occurrence_id}", status_code=303)


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

    occ_repo = MeetingOccurrenceRepository(session)
    occurrence = await occ_repo.get_by_id(item.occurrence_id)
    if occurrence is None:
        raise HTTPException(status_code=404)

    await _ensure_occurrence_owner(session, occurrence, current_user.id)

    _ensure_occurrence_writable(occurrence.id, occurrence.is_completed)

    item.is_done = not item.is_done

    await session.commit()

    log_with_fields(
        logger,
        logging.INFO,
        "agenda item toggled",
        user_id=current_user.id,
        occurrence_id=occurrence.id,
        agenda_item_id=item.id,
        is_done=item.is_done,
    )
    return RedirectResponse(url=f"/occurrences/{occurrence.id}", status_code=303)


@router.post("/occurrences/{occurrence_id}/complete", response_class=RedirectResponse)
async def complete_occurrence(
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
        return RedirectResponse(url=f"/occurrences/{occurrence.id}", status_code=303)

    next_occurrence = await complete_occurrence_and_roll_forward(
        session,
        occurrence=occurrence,
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
    return RedirectResponse(url=f"/occurrences/{redirect_occurrence_id}", status_code=303)

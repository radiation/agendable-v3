from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agendable.auth import require_user
from agendable.db import get_session
from agendable.db.models import AgendaItem, MeetingOccurrenceAttendee, Task, User
from agendable.db.repos import (
    AgendaItemRepository,
    MeetingOccurrenceRepository,
    MeetingSeriesRepository,
    TaskRepository,
    UserRepository,
)
from agendable.recurrence import describe_recurrence
from agendable.web.routes.common import templates

router = APIRouter()


@router.get("/occurrences/{occurrence_id}", response_class=HTMLResponse)
async def occurrence_detail(
    request: Request,
    occurrence_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> HTMLResponse:
    occ_repo = MeetingOccurrenceRepository(session)
    occurrence = await occ_repo.get_by_id(occurrence_id)
    if occurrence is None:
        raise HTTPException(status_code=404)

    series_repo = MeetingSeriesRepository(session)
    series = await series_repo.get_for_owner(occurrence.series_id, current_user.id)
    if series is None:
        raise HTTPException(status_code=404)

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
            "recurrence_label": (
                describe_recurrence(
                    rrule=series.recurrence_rrule,
                    dtstart=series.recurrence_dtstart,
                    timezone=series.recurrence_timezone,
                )
                if series.recurrence_rrule
                else f"Every {series.default_interval_days} days"
            ),
            "occurrence": occurrence,
            "tasks": tasks,
            "agenda_items": agenda_items,
            "attendee_users": attendee_users,
            "current_user": current_user,
        },
    )


@router.post("/occurrences/{occurrence_id}/tasks", response_class=RedirectResponse)
async def create_task(
    occurrence_id: uuid.UUID,
    title: str = Form(...),
    assigned_user_id: uuid.UUID | None = Form(None),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> RedirectResponse:
    occ_repo = MeetingOccurrenceRepository(session)
    occurrence = await occ_repo.get_by_id(occurrence_id)
    if occurrence is None:
        raise HTTPException(status_code=404)

    series_repo = MeetingSeriesRepository(session)
    series = await series_repo.get_for_owner(occurrence.series_id, current_user.id)
    if series is None:
        raise HTTPException(status_code=404)

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

    task = Task(occurrence_id=occurrence_id, title=title, assigned_user_id=final_assignee_id)
    session.add(task)
    await session.commit()
    return RedirectResponse(url=f"/occurrences/{occurrence_id}", status_code=303)


@router.post("/occurrences/{occurrence_id}/attendees", response_class=RedirectResponse)
async def add_attendee(
    occurrence_id: uuid.UUID,
    email: str = Form(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> RedirectResponse:
    occ_repo = MeetingOccurrenceRepository(session)
    occurrence = await occ_repo.get_by_id(occurrence_id)
    if occurrence is None:
        raise HTTPException(status_code=404)

    series_repo = MeetingSeriesRepository(session)
    series = await series_repo.get_for_owner(occurrence.series_id, current_user.id)
    if series is None:
        raise HTTPException(status_code=404)

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

    series_repo = MeetingSeriesRepository(session)
    if await series_repo.get_for_owner(occurrence.series_id, current_user.id) is None:
        raise HTTPException(status_code=404)

    task.is_done = not task.is_done
    await session.commit()
    return RedirectResponse(url=f"/occurrences/{occurrence.id}", status_code=303)


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

    item.is_done = not item.is_done

    occ_repo = MeetingOccurrenceRepository(session)
    occurrence = await occ_repo.get_by_id(item.occurrence_id)
    if occurrence is None:
        raise HTTPException(status_code=404)

    series_repo = MeetingSeriesRepository(session)
    if await series_repo.get_for_owner(occurrence.series_id, current_user.id) is None:
        raise HTTPException(status_code=404)

    await session.commit()
    return RedirectResponse(url=f"/occurrences/{occurrence.id}", status_code=303)


@router.post("/occurrences/{occurrence_id}/complete", response_class=RedirectResponse)
async def complete_occurrence(
    occurrence_id: uuid.UUID,
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

    if occurrence.is_completed:
        return RedirectResponse(url=f"/occurrences/{occurrence.id}", status_code=303)

    next_occurrence = await occ_repo.get_next_for_series(
        occurrence.series_id, occurrence.scheduled_at
    )
    if next_occurrence is not None:
        await session.execute(
            update(Task)
            .where(Task.occurrence_id == occurrence.id, Task.is_done.is_(False))
            .values(occurrence_id=next_occurrence.id)
        )
        await session.execute(
            update(AgendaItem)
            .where(AgendaItem.occurrence_id == occurrence.id, AgendaItem.is_done.is_(False))
            .values(occurrence_id=next_occurrence.id)
        )

    occurrence.is_completed = True
    await session.commit()

    redirect_occurrence_id = next_occurrence.id if next_occurrence is not None else occurrence.id
    return RedirectResponse(url=f"/occurrences/{redirect_occurrence_id}", status_code=303)

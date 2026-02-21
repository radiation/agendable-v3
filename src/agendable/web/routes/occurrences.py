from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.auth import require_user
from agendable.db import get_session
from agendable.db.models import AgendaItem, Task, User
from agendable.db.repos import (
    AgendaItemRepository,
    MeetingOccurrenceRepository,
    MeetingSeriesRepository,
    TaskRepository,
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
            "current_user": current_user,
        },
    )


@router.post("/occurrences/{occurrence_id}/tasks", response_class=RedirectResponse)
async def create_task(
    occurrence_id: uuid.UUID,
    title: str = Form(...),
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

    task = Task(occurrence_id=occurrence_id, title=title)
    session.add(task)
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

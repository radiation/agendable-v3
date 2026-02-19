from __future__ import annotations

import uuid
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.db import get_session
from agendable.models import AgendaItem, MeetingOccurrence, MeetingSeries, Task, User


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


@router.get("/", response_class=HTMLResponse)
async def index(request: Request, session: AsyncSession = Depends(get_session)) -> HTMLResponse:
    result = await session.execute(select(MeetingSeries).order_by(MeetingSeries.created_at.desc()))
    series = list(result.scalars().all())
    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "series": series,
        },
    )


@router.post("/bootstrap", response_class=RedirectResponse)
async def bootstrap(session: AsyncSession = Depends(get_session)) -> RedirectResponse:
    # Minimal bootstrap to avoid building auth on day 1.
    # Creates a single local user if none exists.
    existing = await session.execute(select(User).limit(1))
    if existing.scalar_one_or_none() is None:
        user = User(email="local@example.com", display_name="Local User")
        session.add(user)
        await session.commit()

    return RedirectResponse(url="/", status_code=303)


@router.post("/series", response_class=RedirectResponse)
async def create_series(
    title: str = Form(...),
    default_interval_days: int = Form(7),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    user_result = await session.execute(select(User).order_by(User.created_at.asc()).limit(1))
    owner = user_result.scalar_one_or_none()
    if owner is None:
        raise HTTPException(status_code=400, detail="Run /bootstrap first")

    series = MeetingSeries(
        owner_user_id=owner.id,
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
) -> HTMLResponse:
    series_result = await session.execute(
        select(MeetingSeries).where(MeetingSeries.id == series_id)
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
        },
    )


@router.post("/series/{series_id}/occurrences", response_class=RedirectResponse)
async def create_occurrence(
    series_id: uuid.UUID,
    scheduled_at: str = Form(...),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    series_result = await session.execute(
        select(MeetingSeries).where(MeetingSeries.id == series_id)
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
) -> RedirectResponse:
    task = Task(series_id=series_id, title=title)
    session.add(task)
    await session.commit()
    return RedirectResponse(url=f"/series/{series_id}", status_code=303)


@router.post("/tasks/{task_id}/toggle", response_class=RedirectResponse)
async def toggle_task(
    task_id: uuid.UUID, session: AsyncSession = Depends(get_session)
) -> RedirectResponse:
    result = await session.execute(select(Task).where(Task.id == task_id))
    task = result.scalar_one_or_none()
    if task is None:
        raise HTTPException(status_code=404)

    task.is_done = not task.is_done
    await session.commit()
    return RedirectResponse(url=f"/series/{task.series_id}", status_code=303)


@router.post("/occurrences/{occurrence_id}/agenda", response_class=RedirectResponse)
async def add_agenda_item(
    occurrence_id: uuid.UUID,
    body: str = Form(...),
    session: AsyncSession = Depends(get_session),
) -> RedirectResponse:
    occ_result = await session.execute(
        select(MeetingOccurrence).where(MeetingOccurrence.id == occurrence_id)
    )
    occurrence = occ_result.scalar_one_or_none()
    if occurrence is None:
        raise HTTPException(status_code=404)

    item = AgendaItem(occurrence_id=occurrence_id, body=body)
    session.add(item)
    await session.commit()

    return RedirectResponse(url=f"/series/{occurrence.series_id}", status_code=303)


@router.post("/agenda/{item_id}/toggle", response_class=RedirectResponse)
async def toggle_agenda_item(
    item_id: uuid.UUID, session: AsyncSession = Depends(get_session)
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

    await session.commit()
    return RedirectResponse(url=f"/series/{occurrence.series_id}", status_code=303)

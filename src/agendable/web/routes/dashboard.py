from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agendable.auth import require_user
from agendable.db import get_session
from agendable.db.models import MeetingOccurrence, MeetingSeries, Task, User
from agendable.web.routes.common import templates

router = APIRouter()


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> HTMLResponse:
    now = datetime.now(UTC)

    upcoming_meetings = list(
        (
            await session.execute(
                select(MeetingOccurrence)
                .options(selectinload(MeetingOccurrence.series))
                .join(MeetingSeries, MeetingOccurrence.series_id == MeetingSeries.id)
                .where(
                    MeetingSeries.owner_user_id == current_user.id,
                    MeetingOccurrence.scheduled_at >= now,
                )
                .order_by(MeetingOccurrence.scheduled_at.asc())
                .limit(20)
            )
        )
        .scalars()
        .all()
    )

    outstanding_tasks = list(
        (
            await session.execute(
                select(Task)
                .join(MeetingOccurrence, Task.occurrence_id == MeetingOccurrence.id)
                .join(MeetingSeries, MeetingOccurrence.series_id == MeetingSeries.id)
                .options(
                    selectinload(Task.assignee),
                    selectinload(Task.occurrence).selectinload(MeetingOccurrence.series),
                )
                .where(
                    MeetingSeries.owner_user_id == current_user.id,
                    Task.is_done.is_(False),
                )
                .order_by(Task.due_at.asc(), Task.created_at.asc())
                .limit(200)
            )
        )
        .scalars()
        .all()
    )

    return templates.TemplateResponse(
        request,
        "dashboard.html",
        {
            "upcoming_meetings": upcoming_meetings,
            "outstanding_tasks": outstanding_tasks,
            "current_user": current_user,
        },
    )

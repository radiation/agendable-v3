from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from agendable.auth import require_user
from agendable.db import get_session
from agendable.db.models import (
    AgendaItem,
    MeetingOccurrenceAttendee,
    Task,
    User,
)
from agendable.db.repos import (
    AgendaItemRepository,
    MeetingOccurrenceRepository,
    TaskRepository,
    UserRepository,
)
from agendable.logging_config import log_with_fields
from agendable.services import complete_occurrence_and_roll_forward
from agendable.web.routes.common import templates
from agendable.web.routes.occurrences.access import (
    ensure_occurrence_writable,
    get_accessible_occurrence,
    get_owned_occurrence,
    normalize_optional_text,
    validate_task_assignee,
)
from agendable.web.routes.occurrences.collab import (
    mark_presence,
    record_occurrence_activity,
)
from agendable.web.routes.occurrences.view_context import (
    get_default_task_due_at,
    render_occurrence_detail,
    resolve_task_due_at,
    shared_panel_context,
)

router = APIRouter()
logger = logging.getLogger("agendable.occurrences")


@router.get("/occurrences/{occurrence_id}", response_class=HTMLResponse, name="occurrence_detail")
async def occurrence_detail(
    request: Request,
    occurrence_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> HTMLResponse:
    occurrence, series = await get_accessible_occurrence(session, occurrence_id, current_user.id)
    mark_presence(occurrence_id=occurrence.id, user_id=current_user.id, now=datetime.now(UTC))
    return await render_occurrence_detail(
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
    occurrence, _ = await get_accessible_occurrence(session, occurrence_id, current_user.id)
    context = await shared_panel_context(
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
    occurrence, series = await get_accessible_occurrence(session, occurrence_id, current_user.id)

    ensure_occurrence_writable(occurrence.id, occurrence.is_completed)

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

    final_due_at = await resolve_task_due_at(
        session=session,
        occurrence=occurrence,
        due_at_input=due_at_input,
        timezone=current_user.timezone,
        task_form_errors=task_form_errors,
    )

    final_assignee_id = assigned_user_id or current_user.id
    normalized_description = normalize_optional_text(description_input)
    await validate_task_assignee(
        session=session,
        occurrence_id=occurrence_id,
        series_owner_user_id=series.owner_user_id,
        assignee_id=final_assignee_id,
        task_form_errors=task_form_errors,
    )

    if task_form_errors:
        return await render_occurrence_detail(
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
    record_occurrence_activity(
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
    occurrence, series = await get_owned_occurrence(session, occurrence_id, current_user.id)

    ensure_occurrence_writable(occurrence.id, occurrence.is_completed)

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
        return await render_occurrence_detail(
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
        record_occurrence_activity(
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

    await get_accessible_occurrence(session, occurrence.id, current_user.id)

    ensure_occurrence_writable(occurrence.id, occurrence.is_completed)

    task.is_done = not task.is_done
    await session.commit()
    record_occurrence_activity(
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
    occurrence, series = await get_accessible_occurrence(session, occurrence_id, current_user.id)

    ensure_occurrence_writable(occurrence.id, occurrence.is_completed)

    normalized_body = body.strip()
    normalized_description = normalize_optional_text(description_input)
    if not normalized_body:
        return await render_occurrence_detail(
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
    record_occurrence_activity(
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

    _, series = await get_accessible_occurrence(session, occurrence.id, current_user.id)
    ensure_occurrence_writable(occurrence.id, occurrence.is_completed)

    assignee_errors: dict[str, str] = {}
    await validate_task_assignee(
        session=session,
        occurrence_id=occurrence.id,
        series_owner_user_id=series.owner_user_id,
        assignee_id=assigned_user_id,
        task_form_errors=assignee_errors,
    )
    if assignee_errors:
        raise HTTPException(status_code=400, detail=assignee_errors["assigned_user_id"])

    due_at = await get_default_task_due_at(session, occurrence)
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
    record_occurrence_activity(
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

    await get_accessible_occurrence(session, occurrence.id, current_user.id)

    ensure_occurrence_writable(occurrence.id, occurrence.is_completed)

    item.is_done = not item.is_done

    await session.commit()
    record_occurrence_activity(
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
    occurrence, _ = await get_owned_occurrence(session, occurrence_id, current_user.id)

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
    record_occurrence_activity(
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

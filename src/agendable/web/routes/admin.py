from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.ext.asyncio import AsyncSession

from agendable.auth import require_admin
from agendable.db import get_session
from agendable.db.models import User
from agendable.db.repos import UserRepository
from agendable.web.routes.common import templates

router = APIRouter()


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> HTMLResponse:
    users_repo = UserRepository(session)
    users = await users_repo.list(limit=1000)

    return templates.TemplateResponse(
        request,
        "admin_users.html",
        {
            "users": users,
            "current_user": current_user,
        },
    )

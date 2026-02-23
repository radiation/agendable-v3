from __future__ import annotations

import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from agendable.auth import require_admin
from agendable.db import get_session
from agendable.db.models import User, UserRole
from agendable.db.repos import ExternalIdentityRepository, UserRepository
from agendable.web.routes.common import templates

router = APIRouter()


def _parse_bool(raw: str) -> bool:
    return raw.strip().lower() in {"1", "true", "yes", "on"}


async def _load_user_or_404(users_repo: UserRepository, user_id: uuid.UUID) -> User:
    user = await users_repo.get_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404)
    return user


async def _external_identity_summaries(
    ext_repo: ExternalIdentityRepository,
    *,
    user_ids: list[uuid.UUID],
) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, list[str]]]:
    identities = await ext_repo.list_by_user_ids(user_ids)
    counts: dict[uuid.UUID, int] = defaultdict(int)
    providers: dict[uuid.UUID, list[str]] = defaultdict(list)

    for identity in identities:
        counts[identity.user_id] += 1
        if identity.provider not in providers[identity.user_id]:
            providers[identity.user_id].append(identity.provider)

    return dict(counts), dict(providers)


async def _render_admin_users_template(
    request: Request,
    *,
    session: AsyncSession,
    current_user: User,
    error: str | None,
    status_code: int = 200,
) -> HTMLResponse:
    users_repo = UserRepository(session)
    ext_repo = ExternalIdentityRepository(session)

    users = await users_repo.list(limit=1000)
    user_ids = [user.id for user in users]
    identity_counts, identity_providers = await _external_identity_summaries(
        ext_repo,
        user_ids=user_ids,
    )

    return templates.TemplateResponse(
        request,
        "admin_users.html",
        {
            "users": users,
            "current_user": current_user,
            "error": error,
            "identity_counts": identity_counts,
            "identity_providers": identity_providers,
        },
        status_code=status_code,
    )


@router.get("/admin/users", response_class=HTMLResponse)
async def admin_users(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> HTMLResponse:
    return await _render_admin_users_template(
        request,
        session=session,
        current_user=current_user,
        error=None,
    )


@router.post("/admin/users/{user_id}/role", response_class=RedirectResponse)
async def admin_update_user_role(
    request: Request,
    user_id: uuid.UUID,
    role: str = Form(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> Response:
    users_repo = UserRepository(session)
    user = await _load_user_or_404(users_repo, user_id)

    try:
        new_role = UserRole(role.strip().lower())
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid role") from None

    if user.id == current_user.id and new_role != UserRole.admin:
        return await _render_admin_users_template(
            request,
            session=session,
            current_user=current_user,
            error="You cannot remove your own admin role.",
            status_code=400,
        )

    user.role = new_role
    await session.commit()
    return RedirectResponse(url="/admin/users", status_code=303)


@router.post("/admin/users/{user_id}/active", response_class=RedirectResponse)
async def admin_update_user_active(
    request: Request,
    user_id: uuid.UUID,
    is_active: str = Form(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_admin),
) -> Response:
    users_repo = UserRepository(session)
    user = await _load_user_or_404(users_repo, user_id)

    new_is_active = _parse_bool(is_active)
    if user.id == current_user.id and not new_is_active:
        return await _render_admin_users_template(
            request,
            session=session,
            current_user=current_user,
            error="You cannot deactivate your own account.",
            status_code=400,
        )

    user.is_active = new_is_active
    await session.commit()
    return RedirectResponse(url="/admin/users", status_code=303)

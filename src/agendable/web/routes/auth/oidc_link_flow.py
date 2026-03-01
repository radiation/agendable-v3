from __future__ import annotations

import logging
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from agendable.db.models import ExternalIdentity, User
from agendable.db.repos import ExternalIdentityRepository
from agendable.logging_config import log_with_fields
from agendable.security.audit import audit_oidc_denied, audit_oidc_success
from agendable.security.audit_constants import (
    OIDC_EVENT_IDENTITY_LINK,
    OIDC_REASON_ALREADY_LINKED_OTHER_USER,
    OIDC_REASON_EMAIL_MISMATCH,
)
from agendable.services.oidc_service import resolve_oidc_link_resolution
from agendable.sso.oidc.flow import clear_oidc_link_user_id
from agendable.web.routes import auth as auth_routes

logger = logging.getLogger("uvicorn.error")


def _login_redirect() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=303)


async def _resolve_link_user_or_redirect(
    request: Request,
    *,
    session: AsyncSession,
    link_user_id: uuid.UUID,
) -> User | RedirectResponse:
    try:
        return await auth_routes.get_user_or_404(session, link_user_id)
    except HTTPException:
        clear_oidc_link_user_id(request)
        return RedirectResponse(url="/login", status_code=303)


async def render_link_error(
    request: Request,
    *,
    session: AsyncSession,
    link_user_id: uuid.UUID,
    message: str,
    status_code: int,
) -> Response:
    resolved = await _resolve_link_user_or_redirect(
        request,
        session=session,
        link_user_id=link_user_id,
    )
    if isinstance(resolved, RedirectResponse):
        return resolved

    clear_oidc_link_user_id(request)
    return await auth_routes.render_profile_template(
        request,
        session=session,
        user=resolved,
        identity_error=message,
        status_code=status_code,
    )


async def handle_link_callback(
    request: Request,
    *,
    session: AsyncSession,
    link_user_id: uuid.UUID,
    sub: str,
    email: str,
    debug_oidc: bool,
) -> Response:
    ext_repo = ExternalIdentityRepository(session)

    resolved_link_user = await _resolve_link_user_or_redirect(
        request,
        session=session,
        link_user_id=link_user_id,
    )
    if isinstance(resolved_link_user, RedirectResponse):
        return resolved_link_user

    link_user = resolved_link_user

    link_resolution = await resolve_oidc_link_resolution(
        session,
        link_user=link_user,
        sub=sub,
        email=email,
    )

    if link_resolution.should_redirect_login:
        clear_oidc_link_user_id(request)
        return _login_redirect()

    if link_resolution.error == "already_linked_other_user":
        ext = await ext_repo.get_by_provider_subject("oidc", sub)
        clear_oidc_link_user_id(request)
        audit_oidc_denied(
            event=OIDC_EVENT_IDENTITY_LINK,
            reason=OIDC_REASON_ALREADY_LINKED_OTHER_USER,
            actor=link_user,
            target_user_id=(ext.user_id if ext is not None else None),
        )
        if debug_oidc:
            log_with_fields(
                logger,
                logging.WARNING,
                "oidc link rejected already linked",
                sub=sub,
                requested_user_id=link_user.id,
                existing_user_id=(ext.user_id if ext is not None else None),
            )
        return await auth_routes.render_profile_template(
            request,
            session=session,
            user=link_user,
            identity_error="This SSO account is already linked to a different user.",
            status_code=403,
        )

    if link_resolution.error == "email_mismatch":
        clear_oidc_link_user_id(request)
        audit_oidc_denied(
            event=OIDC_EVENT_IDENTITY_LINK,
            reason=OIDC_REASON_EMAIL_MISMATCH,
            actor=link_user,
            oidc_email=email,
        )
        if debug_oidc:
            log_with_fields(
                logger,
                logging.WARNING,
                "oidc link rejected email mismatch",
                requested_user_id=link_user.id,
                profile_email=link_user.email,
                oidc_email=email,
            )
        return await auth_routes.render_profile_template(
            request,
            session=session,
            user=link_user,
            identity_error="SSO account email must match your profile email.",
            status_code=403,
        )

    if link_resolution.create_identity:
        ext = ExternalIdentity(user_id=link_user.id, provider="oidc", subject=sub, email=email)
        session.add(ext)
        await session.commit()

    clear_oidc_link_user_id(request)
    request.session["user_id"] = str(link_user.id)
    audit_oidc_success(
        event=OIDC_EVENT_IDENTITY_LINK,
        actor=link_user,
    )
    return RedirectResponse(url="/profile", status_code=303)

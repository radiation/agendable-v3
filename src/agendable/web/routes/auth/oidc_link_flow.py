from __future__ import annotations

import logging
import uuid

from fastapi import HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from agendable.db.models import ExternalIdentity, User
from agendable.db.repos import ExternalCalendarConnectionRepository, ExternalIdentityRepository
from agendable.logging_config import log_with_fields
from agendable.security.audit import audit_oidc_denied, audit_oidc_success
from agendable.security.audit_constants import (
    OIDC_EVENT_IDENTITY_LINK,
    OIDC_REASON_ALREADY_LINKED_OTHER_USER,
    OIDC_REASON_EMAIL_MISMATCH,
)
from agendable.services.calendar_connection_service import (
    should_capture_google_calendar_token,
    upsert_google_primary_calendar_connection,
)
from agendable.services.oidc_service import resolve_oidc_link_resolution
from agendable.settings import Settings
from agendable.sso.oidc.flow import OidcTokenCapture, clear_oidc_link_user_id
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


async def _render_already_linked_error(
    request: Request,
    *,
    session: AsyncSession,
    link_user: User,
    identity_provider: str,
    sub: str,
    debug_oidc: bool,
) -> Response:
    ext_repo = ExternalIdentityRepository(session)
    ext = await ext_repo.get_by_provider_subject(identity_provider, sub)
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


async def _render_email_mismatch_error(
    request: Request,
    *,
    session: AsyncSession,
    link_user: User,
    email: str,
    debug_oidc: bool,
) -> Response:
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


async def _maybe_create_identity(
    session: AsyncSession,
    *,
    link_user: User,
    create_identity: bool,
    identity_provider: str,
    sub: str,
    email: str,
) -> None:
    if not create_identity:
        return
    ext = ExternalIdentity(
        user_id=link_user.id, provider=identity_provider, subject=sub, email=email
    )
    session.add(ext)
    await session.flush()


async def _maybe_upsert_google_calendar_connection(
    session: AsyncSession,
    *,
    link_user: User,
    allow_google_calendar_token_capture: bool,
    token_capture: OidcTokenCapture,
    settings: Settings,
) -> None:
    if not allow_google_calendar_token_capture:
        return
    if not should_capture_google_calendar_token(settings=settings, token_capture=token_capture):
        return

    connection_repo = ExternalCalendarConnectionRepository(session)
    await upsert_google_primary_calendar_connection(
        connection_repo=connection_repo,
        user=link_user,
        token_capture=token_capture,
    )


async def handle_link_callback(
    request: Request,
    *,
    session: AsyncSession,
    identity_provider: str,
    allow_google_calendar_token_capture: bool,
    link_user_id: uuid.UUID,
    sub: str,
    email: str,
    debug_oidc: bool,
    token_capture: OidcTokenCapture,
    settings: Settings,
) -> Response:
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
        provider=identity_provider,
        link_user=link_user,
        sub=sub,
        email=email,
    )

    if link_resolution.should_redirect_login:
        clear_oidc_link_user_id(request)
        return _login_redirect()

    if link_resolution.error == "already_linked_other_user":
        return await _render_already_linked_error(
            request,
            session=session,
            link_user=link_user,
            identity_provider=identity_provider,
            sub=sub,
            debug_oidc=debug_oidc,
        )

    if link_resolution.error == "email_mismatch":
        return await _render_email_mismatch_error(
            request,
            session=session,
            link_user=link_user,
            email=email,
            debug_oidc=debug_oidc,
        )

    await _maybe_create_identity(
        session,
        link_user=link_user,
        create_identity=link_resolution.create_identity,
        identity_provider=identity_provider,
        sub=sub,
        email=email,
    )
    await _maybe_upsert_google_calendar_connection(
        session,
        link_user=link_user,
        allow_google_calendar_token_capture=allow_google_calendar_token_capture,
        token_capture=token_capture,
        settings=settings,
    )
    await session.commit()

    clear_oidc_link_user_id(request)
    request.session["user_id"] = str(link_user.id)
    audit_oidc_success(
        event=OIDC_EVENT_IDENTITY_LINK,
        actor=link_user,
    )
    return RedirectResponse(url="/profile", status_code=303)

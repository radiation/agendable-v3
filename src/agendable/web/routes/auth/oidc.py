from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from agendable.auth import require_user, verify_password
from agendable.db import get_session
from agendable.db.models import User
from agendable.db.repos import ExternalIdentityRepository
from agendable.logging_config import log_with_fields
from agendable.security.audit import audit_oidc_denied, audit_oidc_success
from agendable.security.audit_constants import (
    OIDC_EVENT_CALLBACK,
    OIDC_EVENT_IDENTITY_LINK_START,
    OIDC_EVENT_IDENTITY_UNLINK,
    OIDC_REASON_IDENTITY_NOT_FOUND,
    OIDC_REASON_INVALID_PASSWORD,
    OIDC_REASON_ONLY_SIGN_IN_METHOD,
    OIDC_REASON_PROVIDER_DISABLED,
    OIDC_REASON_RATE_LIMITED,
)
from agendable.settings import get_settings
from agendable.sso.oidc.flow import (
    build_authorize_params,
    get_oidc_link_user_id,
    set_oidc_link_user_id,
)
from agendable.web.routes import auth as auth_routes
from agendable.web.routes.auth.oidc_callbacks import (
    auth_oidc_enabled,
    auth_oidc_oauth_client,
    domain_block_response,
    extract_oidc_identity_or_response,
    handle_link_callback,
    handle_login_callback,
    rate_limit_block_response,
)
from agendable.web.routes.auth.rate_limits import is_identity_link_start_rate_limited

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


@router.get("/auth/oidc/start", response_class=RedirectResponse)
async def oidc_start(request: Request) -> Response:
    settings = get_settings()
    if not auth_oidc_enabled():
        if settings.oidc_debug_logging:
            logger.info("OIDC start aborted: provider is disabled")
        raise HTTPException(status_code=404)

    redirect_uri = str(request.url_for("oidc_callback"))
    if settings.oidc_debug_logging:
        log_with_fields(
            logger,
            logging.INFO,
            "oidc start redirect initiated",
            redirect_uri=redirect_uri,
        )
    oidc_client = auth_oidc_oauth_client()
    authorize_params = build_authorize_params(settings.oidc_auth_prompt)

    return await oidc_client.authorize_redirect(request, redirect_uri, **authorize_params)


@router.get("/auth/oidc/callback", name="oidc_callback")
async def oidc_callback(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    settings = get_settings()
    debug_oidc = settings.oidc_debug_logging
    link_user_id = get_oidc_link_user_id(request)

    if not auth_oidc_enabled():
        audit_oidc_denied(event=OIDC_EVENT_CALLBACK, reason=OIDC_REASON_PROVIDER_DISABLED)
        if debug_oidc:
            logger.info("OIDC callback aborted: provider is disabled")
        raise HTTPException(status_code=404)

    identity_or_response = await extract_oidc_identity_or_response(
        request,
        oidc_client=auth_oidc_oauth_client(),
        debug_oidc=debug_oidc,
        link_user_id=link_user_id,
        session=session,
    )
    if isinstance(identity_or_response, Response):
        return identity_or_response

    sub, email, userinfo = identity_or_response

    domain_error = domain_block_response(
        request,
        email=email,
        debug_oidc=debug_oidc,
        allowed_email_domain=settings.allowed_email_domain,
    )
    if domain_error is not None:
        return domain_error

    rate_limit_error = await rate_limit_block_response(
        request,
        settings=settings,
        link_user_id=link_user_id,
        email=email,
        session=session,
    )
    if rate_limit_error is not None:
        return rate_limit_error

    if link_user_id is not None:
        return await handle_link_callback(
            request,
            session=session,
            link_user_id=link_user_id,
            sub=sub,
            email=email,
            debug_oidc=debug_oidc,
        )

    return await handle_login_callback(
        request,
        session=session,
        sub=sub,
        email=email,
        userinfo=userinfo,
        debug_oidc=debug_oidc,
        link_user_id=link_user_id,
    )


@router.post(
    "/profile/identities/link/start",
    response_class=RedirectResponse,
)
async def start_profile_identity_link(
    request: Request,
    password: str = Form(""),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> Response:
    if not auth_oidc_enabled():
        audit_oidc_denied(
            event=OIDC_EVENT_IDENTITY_LINK_START,
            reason=OIDC_REASON_PROVIDER_DISABLED,
            actor=current_user,
        )
        raise HTTPException(status_code=404)

    user = await auth_routes.get_user_or_404(session, current_user.id)
    if is_identity_link_start_rate_limited(request, user_id=user.id):
        audit_oidc_denied(
            event=OIDC_EVENT_IDENTITY_LINK_START,
            reason=OIDC_REASON_RATE_LIMITED,
            actor=user,
        )
        return await auth_routes.render_profile_template(
            request,
            session=session,
            user=user,
            identity_error="Too many link attempts. Try again in a minute.",
            status_code=429,
        )

    if user.password_hash is not None and not verify_password(password, user.password_hash):
        audit_oidc_denied(
            event=OIDC_EVENT_IDENTITY_LINK_START,
            reason=OIDC_REASON_INVALID_PASSWORD,
            actor=user,
        )
        return await auth_routes.render_profile_template(
            request,
            session=session,
            user=user,
            identity_error="Enter your current password to link an SSO account.",
            status_code=401,
        )

    set_oidc_link_user_id(request, user.id)
    audit_oidc_success(event=OIDC_EVENT_IDENTITY_LINK_START, actor=user)
    return RedirectResponse(url="/auth/oidc/start", status_code=303)


@router.post(
    "/profile/identities/{identity_id}/unlink",
    response_class=RedirectResponse,
)
async def unlink_profile_identity(
    request: Request,
    identity_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> Response:
    user = await auth_routes.get_user_or_404(session, current_user.id)
    ext_repo = ExternalIdentityRepository(session)

    identity = await ext_repo.get(identity_id)
    if identity is None or identity.user_id != user.id:
        audit_oidc_denied(
            event=OIDC_EVENT_IDENTITY_UNLINK,
            reason=OIDC_REASON_IDENTITY_NOT_FOUND,
            actor=user,
            target_identity_id=identity_id,
        )
        raise HTTPException(status_code=404)

    identities = await ext_repo.list_by_user_id(user.id)
    if user.password_hash is None and len(identities) <= 1:
        audit_oidc_denied(
            event=OIDC_EVENT_IDENTITY_UNLINK,
            reason=OIDC_REASON_ONLY_SIGN_IN_METHOD,
            actor=user,
            target_identity_id=identity.id,
        )
        return await auth_routes.render_profile_template(
            request,
            session=session,
            user=user,
            identity_error="You cannot unlink your only sign-in method.",
            status_code=400,
        )

    await ext_repo.delete(identity, flush=False)
    await session.commit()
    audit_oidc_success(
        event=OIDC_EVENT_IDENTITY_UNLINK,
        actor=user,
        target_identity_id=identity.id,
    )
    return RedirectResponse(url="/profile", status_code=303)

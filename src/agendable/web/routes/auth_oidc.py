from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from typing import Any, cast

from authlib.integrations.starlette_client import OAuthError
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from agendable.auth import require_user, verify_password
from agendable.db import get_session
from agendable.db.models import ExternalIdentity, User
from agendable.db.repos import ExternalIdentityRepository
from agendable.logging_config import log_with_fields
from agendable.services.oidc_service import (
    resolve_oidc_link_resolution,
    resolve_oidc_login_resolution,
)
from agendable.settings import get_settings
from agendable.sso_oidc_flow import (
    OidcIdentityClaims,
    build_authorize_params,
    clear_oidc_link_user_id,
    get_oidc_link_user_id,
    parse_identity_claims,
    parse_userinfo_from_token,
    set_oidc_link_user_id,
)
from agendable.web.routes import auth as auth_routes

router = APIRouter()
logger = logging.getLogger("uvicorn.error")
_OIDC_ENABLED_ATTR = "oidc_enabled"
_OIDC_CLIENT_ATTR = "_oidc_oauth_client"


def _oidc_enabled() -> bool:
    enabled_fn = cast(Callable[[], bool], getattr(auth_routes, _OIDC_ENABLED_ATTR))
    return enabled_fn()


def _oidc_oauth_client() -> Any:
    oidc_client_fn = cast(Callable[[], Any], getattr(auth_routes, _OIDC_CLIENT_ATTR))
    return oidc_client_fn()


@router.get("/auth/oidc/start", response_class=RedirectResponse)
async def oidc_start(request: Request) -> Response:
    settings = get_settings()
    if not _oidc_enabled():
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
    oidc_client = _oidc_oauth_client()
    authorize_params = build_authorize_params(settings.oidc_auth_prompt)

    return cast(
        Response,
        await oidc_client.authorize_redirect(request, redirect_uri, **authorize_params),
    )


async def _resolve_link_user_or_redirect(
    request: Request,
    *,
    session: AsyncSession,
    link_user_id: uuid.UUID,
) -> User | RedirectResponse:
    try:
        return await auth_routes._get_user_or_404(session, link_user_id)
    except HTTPException:
        clear_oidc_link_user_id(request)
        return RedirectResponse(url="/login", status_code=303)


async def _render_link_error(
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
    return await auth_routes._render_profile_template(
        request,
        session=session,
        user=resolved,
        identity_error=message,
        status_code=status_code,
    )


@router.get("/auth/oidc/callback", name="oidc_callback")
async def oidc_callback(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    settings = get_settings()
    debug_oidc = settings.oidc_debug_logging
    link_user_id = get_oidc_link_user_id(request)

    if not _oidc_enabled():
        if debug_oidc:
            logger.info("OIDC callback aborted: provider is disabled")
        raise HTTPException(status_code=404)

    oidc_client = _oidc_oauth_client()

    try:
        token = await oidc_client.authorize_access_token(request)
    except OAuthError:
        if debug_oidc:
            logger.info("OIDC callback OAuthError during token/id token exchange")
        if link_user_id is not None:
            return await _render_link_error(
                request,
                session=session,
                link_user_id=link_user_id,
                message="SSO linking was cancelled or failed.",
                status_code=400,
            )
        return RedirectResponse(url="/login", status_code=303)

    token_keys: list[str] = [str(key) for key in token]
    if debug_oidc:
        logger.info("OIDC callback token keys=%s", sorted(token_keys))

    userinfo = await parse_userinfo_from_token(oidc_client, request, token)
    claims: OidcIdentityClaims = parse_identity_claims(userinfo)
    sub = claims.sub
    email = claims.email
    email_verified = claims.email_verified

    if debug_oidc:
        log_with_fields(
            logger,
            logging.INFO,
            "oidc callback claims parsed",
            sub_present=bool(sub),
            email=email,
            email_verified=email_verified,
            claim_keys=sorted(userinfo.keys()),
        )

    if not sub or not email or not email_verified:
        if debug_oidc:
            logger.info(
                "OIDC callback rejected claims: sub_present=%s email_present=%s email_verified=%s",
                bool(sub),
                bool(email),
                email_verified,
            )
        if link_user_id is not None:
            return await _render_link_error(
                request,
                session=session,
                link_user_id=link_user_id,
                message="SSO provider did not return required identity claims.",
                status_code=403,
            )
        return RedirectResponse(url="/login", status_code=303)

    if settings.allowed_email_domain is not None:
        allowed = settings.allowed_email_domain.strip().lower().lstrip("@")
        if not email.endswith(f"@{allowed}"):
            if debug_oidc:
                logger.info(
                    "OIDC callback denied by allowed_email_domain: email=%s allowed_domain=%s",
                    email,
                    allowed,
                )
            return auth_routes._render_login_template(
                request,
                error="Email domain not allowed",
                status_code=403,
            )

    ext_repo = ExternalIdentityRepository(session)

    if link_user_id is not None:
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
            return RedirectResponse(url="/login", status_code=303)

        if link_resolution.error == "already_linked_other_user":
            ext = await ext_repo.get_by_provider_subject("oidc", sub)
            clear_oidc_link_user_id(request)
            if debug_oidc:
                log_with_fields(
                    logger,
                    logging.WARNING,
                    "oidc link rejected already linked",
                    sub=sub,
                    requested_user_id=link_user.id,
                    existing_user_id=(ext.user_id if ext is not None else None),
                )
            return await auth_routes._render_profile_template(
                request,
                session=session,
                user=link_user,
                identity_error="This SSO account is already linked to a different user.",
                status_code=403,
            )

        if link_resolution.error == "email_mismatch":
            clear_oidc_link_user_id(request)
            if debug_oidc:
                log_with_fields(
                    logger,
                    logging.WARNING,
                    "oidc link rejected email mismatch",
                    requested_user_id=link_user.id,
                    profile_email=link_user.email,
                    oidc_email=email,
                )
            return await auth_routes._render_profile_template(
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
        return RedirectResponse(url="/profile", status_code=303)

    login_resolution = await resolve_oidc_login_resolution(
        session,
        sub=sub,
        email=email,
        userinfo=userinfo,
        is_bootstrap_admin_email=auth_routes._is_bootstrap_admin_email,
    )

    if login_resolution.should_redirect_login:
        if debug_oidc:
            logger.info("OIDC callback identity points to missing user")
        return RedirectResponse(url="/login", status_code=303)

    if login_resolution.error == "inactive_user":
        if debug_oidc:
            logger.info("OIDC callback denied inactive user for email=%s", email)
        return auth_routes._render_login_template(
            request,
            error="This account is deactivated. Contact an admin.",
            status_code=403,
        )

    if login_resolution.error == "password_user_requires_link":
        if debug_oidc:
            logger.info(
                "OIDC callback denied linking password-based account for email=%s",
                email,
            )
        return auth_routes._render_login_template(
            request,
            error="An account with this email already exists. Sign in with password first to link SSO.",
            status_code=403,
        )

    user = login_resolution.user
    if user is None:
        return RedirectResponse(url="/login", status_code=303)

    if login_resolution.create_identity:
        if debug_oidc:
            logger.info("OIDC callback linking or creating SSO identity for email=%s", email)
        ext = ExternalIdentity(user_id=user.id, provider="oidc", subject=sub, email=email)
        session.add(ext)
        await session.commit()

    if debug_oidc:
        log_with_fields(
            logger,
            logging.INFO,
            "oidc callback success",
            user_id=user.id,
            email=user.email,
            link_mode=link_user_id is not None,
        )

    await auth_routes._maybe_promote_bootstrap_admin(user, session)

    request.session["user_id"] = str(user.id)
    return RedirectResponse(url="/dashboard", status_code=303)


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
    if not _oidc_enabled():
        raise HTTPException(status_code=404)

    user = await auth_routes._get_user_or_404(session, current_user.id)
    if user.password_hash is not None and not verify_password(password, user.password_hash):
        return await auth_routes._render_profile_template(
            request,
            session=session,
            user=user,
            identity_error="Enter your current password to link an SSO account.",
            status_code=401,
        )

    set_oidc_link_user_id(request, user.id)
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
    user = await auth_routes._get_user_or_404(session, current_user.id)
    ext_repo = ExternalIdentityRepository(session)

    identity = await ext_repo.get(identity_id)
    if identity is None or identity.user_id != user.id:
        raise HTTPException(status_code=404)

    identities = await ext_repo.list_by_user_id(user.id)
    if user.password_hash is None and len(identities) <= 1:
        return await auth_routes._render_profile_template(
            request,
            session=session,
            user=user,
            identity_error="You cannot unlink your only sign-in method.",
            status_code=400,
        )

    await ext_repo.delete(identity, flush=False)
    await session.commit()
    return RedirectResponse(url="/profile", status_code=303)

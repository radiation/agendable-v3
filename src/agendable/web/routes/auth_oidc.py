from __future__ import annotations

import logging
import uuid
from collections.abc import Callable, Mapping
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
from agendable.security_audit import audit_oidc_denied, audit_oidc_success
from agendable.services.oidc_service import (
    is_email_allowed_for_domain,
    oidc_login_error_message,
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
from agendable.web.routes.auth_rate_limits import (
    is_identity_link_start_rate_limited,
    is_oidc_callback_rate_limited,
)

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


def _audit_callback_denied(
    *,
    reason: str,
    actor_email: str | None = None,
    link_mode: bool | None = None,
) -> None:
    audit_oidc_denied(
        event="callback",
        reason=reason,
        actor_email=actor_email,
        link_mode=link_mode,
    )


def _audit_identity_link_start(
    *,
    outcome: str,
    actor: User,
    reason: str | None = None,
) -> None:
    if outcome == "denied":
        if reason is None:
            raise ValueError("reason is required for denied identity link start events")
        audit_oidc_denied(event="identity_link_start", reason=reason, actor=actor)
        return
    audit_oidc_success(event="identity_link_start", actor=actor)


def _auth_oidc_enabled() -> bool:
    routes_mod = cast(Any, auth_routes)
    enabled_fn = cast(
        Callable[[], bool],
        routes_mod._oidc_enabled
        if hasattr(routes_mod, "_oidc_enabled")
        else routes_mod.oidc_enabled,
    )
    return enabled_fn()


def _auth_oidc_oauth_client() -> Any:
    routes_mod = cast(Any, auth_routes)
    client_fn = cast(
        Callable[[], Any],
        routes_mod._oidc_oauth_client
        if hasattr(routes_mod, "_oidc_oauth_client")
        else routes_mod.oidc_oauth_client,
    )
    return client_fn()


def _login_redirect() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=303)


@router.get("/auth/oidc/start", response_class=RedirectResponse)
async def oidc_start(request: Request) -> Response:
    settings = get_settings()
    if not _auth_oidc_enabled():
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
    oidc_client = _auth_oidc_oauth_client()
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
        return await auth_routes.get_user_or_404(session, link_user_id)
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
    return await auth_routes.render_profile_template(
        request,
        session=session,
        user=resolved,
        identity_error=message,
        status_code=status_code,
    )


async def _handle_link_callback(
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
            event="identity_link",
            reason="already_linked_other_user",
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
            event="identity_link",
            reason="email_mismatch",
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
        event="identity_link",
        actor=link_user,
    )
    return RedirectResponse(url="/profile", status_code=303)


async def _handle_login_callback(
    request: Request,
    *,
    session: AsyncSession,
    sub: str,
    email: str,
    userinfo: Mapping[str, object],
    debug_oidc: bool,
    link_user_id: uuid.UUID | None,
) -> Response:
    login_resolution = await resolve_oidc_login_resolution(
        session,
        sub=sub,
        email=email,
        userinfo=userinfo,
        is_bootstrap_admin_email=auth_routes.is_bootstrap_admin_email,
    )

    user_or_response = await _resolve_login_user_or_response(
        request,
        session=session,
        login_resolution=login_resolution,
        email=email,
        debug_oidc=debug_oidc,
    )
    if isinstance(user_or_response, Response):
        return user_or_response
    user = user_or_response

    await _create_login_identity_if_needed(
        session=session,
        user=user,
        create_identity=login_resolution.create_identity,
        sub=sub,
        email=email,
        debug_oidc=debug_oidc,
    )

    if debug_oidc:
        log_with_fields(
            logger,
            logging.INFO,
            "oidc callback success",
            user_id=user.id,
            email=user.email,
            link_mode=link_user_id is not None,
        )

    await auth_routes.maybe_promote_bootstrap_admin(user, session)

    request.session["user_id"] = str(user.id)
    audit_oidc_success(
        event="callback_login",
        actor=user,
        link_mode=link_user_id is not None,
    )
    return RedirectResponse(url="/dashboard", status_code=303)


async def _resolve_login_user_or_response(
    request: Request,
    *,
    session: AsyncSession,
    login_resolution: object,
    email: str,
    debug_oidc: bool,
) -> User | Response:
    resolved = cast(Any, login_resolution)

    if resolved.should_redirect_login:
        if debug_oidc:
            logger.info("OIDC callback identity points to missing user")
        return _login_redirect()

    error_message = oidc_login_error_message(resolved.error)
    if error_message is not None:
        audit_oidc_denied(
            event="callback_login",
            reason=resolved.error,
            actor_email=email,
        )
        if debug_oidc and resolved.error == "inactive_user":
            logger.info("OIDC callback denied inactive user for email=%s", email)
        if debug_oidc and resolved.error == "password_user_requires_link":
            logger.info("OIDC callback denied linking password-based account")
        return auth_routes.render_login_template(
            request,
            error=error_message,
            status_code=403,
        )

    user = cast(User | None, resolved.user)
    if user is None:
        return _login_redirect()
    return user


async def _create_login_identity_if_needed(
    *,
    session: AsyncSession,
    user: User,
    create_identity: bool,
    sub: str,
    email: str,
    debug_oidc: bool,
) -> None:
    if not create_identity:
        return

    if debug_oidc:
        logger.info("OIDC callback linking or creating SSO identity for email=%s", email)
    ext = ExternalIdentity(user_id=user.id, provider="oidc", subject=sub, email=email)
    session.add(ext)
    await session.commit()


async def _exchange_token_or_error(
    request: Request,
    *,
    oidc_client: Any,
    debug_oidc: bool,
    link_user_id: uuid.UUID | None,
    session: AsyncSession,
) -> dict[str, object] | Response:
    try:
        token = await oidc_client.authorize_access_token(request)
    except OAuthError:
        _audit_callback_denied(reason="oauth_error", link_mode=link_user_id is not None)
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
        return _login_redirect()

    return cast(dict[str, object], token)


async def _parse_and_validate_claims_or_error(
    request: Request,
    *,
    oidc_client: Any,
    token: dict[str, object],
    debug_oidc: bool,
    link_user_id: uuid.UUID | None,
    session: AsyncSession,
) -> tuple[str, str, Mapping[str, object]] | Response:
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

    if sub and email and email_verified:
        return sub, email, userinfo

    if debug_oidc:
        logger.info(
            "OIDC callback rejected claims: sub_present=%s email_present=%s email_verified=%s",
            bool(sub),
            bool(email),
            email_verified,
        )
    _audit_callback_denied(
        reason="missing_required_claims",
        actor_email=email,
        link_mode=link_user_id is not None,
    )
    if link_user_id is not None:
        return await _render_link_error(
            request,
            session=session,
            link_user_id=link_user_id,
            message="SSO provider did not return required identity claims.",
            status_code=403,
        )
    return _login_redirect()


async def _extract_oidc_identity_or_response(
    request: Request,
    *,
    oidc_client: Any,
    debug_oidc: bool,
    link_user_id: uuid.UUID | None,
    session: AsyncSession,
) -> tuple[str, str, Mapping[str, object]] | Response:
    token_or_response = await _exchange_token_or_error(
        request,
        oidc_client=oidc_client,
        debug_oidc=debug_oidc,
        link_user_id=link_user_id,
        session=session,
    )
    if isinstance(token_or_response, Response):
        return token_or_response

    claims_or_response = await _parse_and_validate_claims_or_error(
        request,
        oidc_client=oidc_client,
        token=token_or_response,
        debug_oidc=debug_oidc,
        link_user_id=link_user_id,
        session=session,
    )
    if isinstance(claims_or_response, Response):
        return claims_or_response

    return claims_or_response


def _domain_block_response(
    request: Request,
    *,
    email: str,
    debug_oidc: bool,
    allowed_email_domain: str | None,
) -> Response | None:
    if is_email_allowed_for_domain(email, allowed_email_domain):
        return None

    if debug_oidc:
        allowed_value = allowed_email_domain or ""
        allowed = allowed_value.strip().lower().lstrip("@")
        logger.info(
            "OIDC callback denied by allowed_email_domain: email=%s allowed_domain=%s",
            email,
            allowed,
        )
    _audit_callback_denied(reason="domain_not_allowed", actor_email=email)
    return auth_routes.render_login_template(
        request,
        error="Email domain not allowed",
        status_code=403,
    )


async def _rate_limit_block_response(
    request: Request,
    *,
    settings: Any,
    link_user_id: uuid.UUID | None,
    email: str,
    session: AsyncSession,
) -> Response | None:
    account_key = str(link_user_id) if link_user_id is not None else email.strip().lower()
    if not is_oidc_callback_rate_limited(request, settings=settings, account_key=account_key):
        return None

    _audit_callback_denied(
        reason="rate_limited",
        actor_email=email,
        link_mode=link_user_id is not None,
    )

    if link_user_id is not None:
        return await _render_link_error(
            request,
            session=session,
            link_user_id=link_user_id,
            message="Too many SSO attempts. Try again in a minute.",
            status_code=429,
        )

    return auth_routes.render_login_template(
        request,
        error="Too many SSO attempts. Try again in a minute.",
        status_code=429,
    )


@router.get("/auth/oidc/callback", name="oidc_callback")
async def oidc_callback(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    settings = get_settings()
    debug_oidc = settings.oidc_debug_logging
    link_user_id = get_oidc_link_user_id(request)

    if not _auth_oidc_enabled():
        _audit_callback_denied(reason="provider_disabled")
        if debug_oidc:
            logger.info("OIDC callback aborted: provider is disabled")
        raise HTTPException(status_code=404)

    identity_or_response = await _extract_oidc_identity_or_response(
        request,
        oidc_client=_auth_oidc_oauth_client(),
        debug_oidc=debug_oidc,
        link_user_id=link_user_id,
        session=session,
    )
    if isinstance(identity_or_response, Response):
        return identity_or_response

    sub, email, userinfo = identity_or_response

    domain_error = _domain_block_response(
        request,
        email=email,
        debug_oidc=debug_oidc,
        allowed_email_domain=settings.allowed_email_domain,
    )
    if domain_error is not None:
        return domain_error

    rate_limit_error = await _rate_limit_block_response(
        request,
        settings=settings,
        link_user_id=link_user_id,
        email=email,
        session=session,
    )
    if rate_limit_error is not None:
        return rate_limit_error

    if link_user_id is not None:
        return await _handle_link_callback(
            request,
            session=session,
            link_user_id=link_user_id,
            sub=sub,
            email=email,
            debug_oidc=debug_oidc,
        )

    return await _handle_login_callback(
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
    if not _auth_oidc_enabled():
        _audit_identity_link_start(
            outcome="denied",
            actor=current_user,
            reason="provider_disabled",
        )
        raise HTTPException(status_code=404)

    user = await auth_routes.get_user_or_404(session, current_user.id)
    if is_identity_link_start_rate_limited(request, user_id=user.id):
        _audit_identity_link_start(outcome="denied", actor=user, reason="rate_limited")
        return await auth_routes.render_profile_template(
            request,
            session=session,
            user=user,
            identity_error="Too many link attempts. Try again in a minute.",
            status_code=429,
        )

    if user.password_hash is not None and not verify_password(password, user.password_hash):
        _audit_identity_link_start(outcome="denied", actor=user, reason="invalid_password")
        return await auth_routes.render_profile_template(
            request,
            session=session,
            user=user,
            identity_error="Enter your current password to link an SSO account.",
            status_code=401,
        )

    set_oidc_link_user_id(request, user.id)
    _audit_identity_link_start(outcome="success", actor=user)
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
            event="identity_unlink",
            reason="identity_not_found",
            actor=user,
            target_identity_id=identity_id,
        )
        raise HTTPException(status_code=404)

    identities = await ext_repo.list_by_user_id(user.id)
    if user.password_hash is None and len(identities) <= 1:
        audit_oidc_denied(
            event="identity_unlink",
            reason="only_sign_in_method",
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
        event="identity_unlink",
        actor=user,
        target_identity_id=identity.id,
    )
    return RedirectResponse(url="/profile", status_code=303)

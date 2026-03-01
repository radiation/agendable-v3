from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any, cast

from authlib.integrations.starlette_client import OAuthError
from fastapi import Request
from fastapi.responses import RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from agendable.db.models import ExternalIdentity, User
from agendable.logging_config import log_with_fields
from agendable.security_audit import audit_oidc_denied, audit_oidc_success
from agendable.services.oidc_service import (
    OidcLoginResolution,
    is_email_allowed_for_domain,
    oidc_login_error_message,
    resolve_oidc_login_resolution,
)
from agendable.sso_oidc_flow import (
    OidcIdentityClaims,
    parse_identity_claims,
    parse_userinfo_from_token,
)
from agendable.web.routes import auth as auth_routes
from agendable.web.routes.auth.oidc_link_flow import render_link_error
from agendable.web.routes.auth.rate_limits import is_oidc_callback_rate_limited

logger = logging.getLogger("uvicorn.error")


def audit_callback_denied(
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


def auth_oidc_enabled() -> bool:
    enabled_fn = getattr(auth_routes, "_oidc_enabled", auth_routes.oidc_enabled)
    return enabled_fn()


def auth_oidc_oauth_client() -> Any:
    client_fn = getattr(auth_routes, "_oidc_oauth_client", auth_routes.oidc_oauth_client)
    return client_fn()


def _login_redirect() -> RedirectResponse:
    return RedirectResponse(url="/login", status_code=303)


async def handle_login_callback(
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
    login_resolution: OidcLoginResolution,
    email: str,
    debug_oidc: bool,
) -> User | Response:
    if login_resolution.should_redirect_login:
        if debug_oidc:
            logger.info("OIDC callback identity points to missing user")
        return _login_redirect()

    error_message = oidc_login_error_message(login_resolution.error)
    if error_message is not None:
        if login_resolution.error is None:
            raise ValueError("OIDC login resolution error_message requires a non-None error code")
        audit_oidc_denied(
            event="callback_login",
            reason=login_resolution.error,
            actor_email=email,
        )
        if debug_oidc and login_resolution.error == "inactive_user":
            logger.info("OIDC callback denied inactive user for email=%s", email)
        if debug_oidc and login_resolution.error == "password_user_requires_link":
            logger.info("OIDC callback denied linking password-based account")
        return auth_routes.render_login_template(
            request,
            error=error_message,
            status_code=403,
        )

    user = login_resolution.user
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
        audit_callback_denied(reason="oauth_error", link_mode=link_user_id is not None)
        if debug_oidc:
            logger.info("OIDC callback OAuthError during token/id token exchange")
        if link_user_id is not None:
            return await render_link_error(
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
    audit_callback_denied(
        reason="missing_required_claims",
        actor_email=email,
        link_mode=link_user_id is not None,
    )
    if link_user_id is not None:
        return await render_link_error(
            request,
            session=session,
            link_user_id=link_user_id,
            message="SSO provider did not return required identity claims.",
            status_code=403,
        )
    return _login_redirect()


async def extract_oidc_identity_or_response(
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


def domain_block_response(
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
    audit_callback_denied(reason="domain_not_allowed", actor_email=email)
    return auth_routes.render_login_template(
        request,
        error="Email domain not allowed",
        status_code=403,
    )


async def rate_limit_block_response(
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

    audit_callback_denied(
        reason="rate_limited",
        actor_email=email,
        link_mode=link_user_id is not None,
    )

    if link_user_id is not None:
        return await render_link_error(
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

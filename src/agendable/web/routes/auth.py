from __future__ import annotations

import logging
import uuid
from collections.abc import Mapping
from typing import Any, cast

from authlib.integrations.starlette_client import OAuthError
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from agendable.auth import hash_password, require_user, verify_password
from agendable.db import get_session
from agendable.db.models import ExternalIdentity, User, UserRole
from agendable.db.repos import ExternalIdentityRepository, UserRepository
from agendable.settings import get_settings
from agendable.sso_oidc import oidc_enabled
from agendable.web.routes.common import oauth, parse_timezone, templates

router = APIRouter()
logger = logging.getLogger("uvicorn.error")


def _is_bootstrap_admin_email(email: str) -> bool:
    configured = get_settings().bootstrap_admin_email
    if configured is None:
        return False
    return configured.strip().lower() == email.strip().lower()


async def _maybe_promote_bootstrap_admin(user: User, session: AsyncSession) -> None:
    if user.role == UserRole.admin:
        return
    if not _is_bootstrap_admin_email(user.email):
        return

    user.role = UserRole.admin
    await session.commit()


def _render_login_template(
    request: Request,
    *,
    error: str | None,
    status_code: int = 200,
) -> Response:
    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "error": error,
            "current_user": None,
            "oidc_enabled": oidc_enabled(),
        },
        status_code=status_code,
    )


def _signup_form_context(
    *,
    first_name: str = "",
    last_name: str = "",
    timezone: str = "UTC",
    email: str = "",
) -> dict[str, str]:
    return {
        "first_name": first_name,
        "last_name": last_name,
        "timezone": timezone,
        "email": email,
    }


def _render_signup_template(
    request: Request,
    *,
    error: str | None,
    form: dict[str, str] | None = None,
    status_code: int = 200,
) -> Response:
    return templates.TemplateResponse(
        request,
        "signup.html",
        {
            "error": error,
            "current_user": None,
            "form": form or _signup_form_context(),
        },
        status_code=status_code,
    )


async def _redirect_if_authenticated(
    request: Request,
    session: AsyncSession,
) -> RedirectResponse | None:
    try:
        _ = await require_user(request, session)
        return RedirectResponse(url="/dashboard", status_code=303)
    except HTTPException:
        return None


async def _get_user_or_404(session: AsyncSession, user_id: uuid.UUID) -> User:
    users = UserRepository(session)
    user = await users.get_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=404)
    return user


def _oidc_oauth_client() -> Any:
    return cast(Any, oauth).oidc


def _as_userinfo_mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value)


def _claim_is_truthy(value: object) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"true", "1", "yes", "on"}
    if isinstance(value, int):
        return value != 0
    return False


def _userinfo_name_parts(userinfo: Mapping[str, object], email: str) -> tuple[str, str]:
    first_name = str(userinfo.get("given_name", "")).strip()
    last_name = str(userinfo.get("family_name", "")).strip()

    if not first_name and not last_name:
        full_name = str(userinfo.get("name", "")).strip()
        if full_name:
            parts = full_name.split(maxsplit=1)
            first_name = parts[0]
            if len(parts) > 1:
                last_name = parts[1]

    if not first_name:
        first_name = email.split("@", 1)[0] or "User"

    return first_name, last_name


async def _provision_user_for_oidc(
    session: AsyncSession,
    *,
    email: str,
    userinfo: Mapping[str, object],
) -> User:
    first_name, last_name = _userinfo_name_parts(userinfo, email)
    user = User(
        email=email,
        first_name=first_name,
        last_name=last_name,
        display_name=f"{first_name} {last_name}".strip(),
        timezone="UTC",
        role=(UserRole.admin if _is_bootstrap_admin_email(email) else UserRole.user),
        password_hash=None,
    )
    session.add(user)
    await session.flush()
    return user


@router.get("/login", response_class=Response)
async def login_form(request: Request, session: AsyncSession = Depends(get_session)) -> Response:
    redirect_response = await _redirect_if_authenticated(request, session)
    if redirect_response is not None:
        return redirect_response

    return _render_login_template(request, error=None)


@router.post("/login", response_class=HTMLResponse)
async def login(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_session),
) -> Response:
    normalized_email = email.strip().lower()

    users = UserRepository(session)
    user = await users.get_by_email(normalized_email)

    if user is None:
        return _render_login_template(
            request,
            error="Account not found. Create one first.",
            status_code=401,
        )

    if not user.is_active:
        return _render_login_template(
            request,
            error="This account is deactivated. Contact an admin.",
            status_code=403,
        )

    if user.password_hash is None or not verify_password(password, user.password_hash):
        return _render_login_template(
            request,
            error="Invalid email or password",
            status_code=401,
        )

    await _maybe_promote_bootstrap_admin(user, session)

    request.session["user_id"] = str(user.id)
    return RedirectResponse(url="/dashboard", status_code=303)


@router.get("/signup", response_class=Response)
async def signup_form(request: Request, session: AsyncSession = Depends(get_session)) -> Response:
    redirect_response = await _redirect_if_authenticated(request, session)
    if redirect_response is not None:
        return redirect_response

    return _render_signup_template(request, error=None)


@router.post("/signup", response_class=HTMLResponse)
async def signup(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(""),
    timezone: str = Form("UTC"),
    email: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_session),
) -> Response:
    normalized_first_name = first_name.strip()
    normalized_last_name = last_name.strip()
    timezone_input = timezone.strip() or "UTC"
    normalized_timezone = parse_timezone(timezone_input).key
    normalized_email = email.strip().lower()
    if not normalized_first_name:
        raise HTTPException(status_code=400, detail="First name is required")
    if not normalized_email:
        raise HTTPException(status_code=400, detail="Email is required")

    users = UserRepository(session)
    existing = await users.get_by_email(normalized_email)
    if existing is not None:
        return _render_signup_template(
            request,
            error="Account already exists. Sign in instead.",
            form=_signup_form_context(
                first_name=normalized_first_name,
                last_name=normalized_last_name,
                timezone=normalized_timezone,
                email=normalized_email,
            ),
            status_code=400,
        )

    user = User(
        email=normalized_email,
        first_name=normalized_first_name,
        last_name=normalized_last_name,
        timezone=normalized_timezone,
        display_name=f"{normalized_first_name} {normalized_last_name}".strip(),
        role=(UserRole.admin if _is_bootstrap_admin_email(normalized_email) else UserRole.user),
        password_hash=hash_password(password),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    request.session["user_id"] = str(user.id)
    return RedirectResponse(url="/dashboard", status_code=303)


@router.get("/auth/oidc/start", response_class=RedirectResponse)
async def oidc_start(request: Request) -> Response:
    settings = get_settings()
    if not oidc_enabled():
        if settings.oidc_debug_logging:
            logger.info("OIDC start aborted: provider is disabled")
        raise HTTPException(status_code=404)

    redirect_uri = str(request.url_for("oidc_callback"))
    if settings.oidc_debug_logging:
        logger.info("OIDC start redirect initiated: redirect_uri=%s", redirect_uri)
    oidc_client = _oidc_oauth_client()
    prompt = (settings.oidc_auth_prompt or "").strip()
    authorize_params: dict[str, str] = {}
    if prompt:
        authorize_params["prompt"] = prompt

    return cast(
        Response,
        await oidc_client.authorize_redirect(request, redirect_uri, **authorize_params),
    )


@router.get("/auth/oidc/callback", name="oidc_callback")
async def oidc_callback(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    settings = get_settings()
    debug_oidc = settings.oidc_debug_logging

    if not oidc_enabled():
        if debug_oidc:
            logger.info("OIDC callback aborted: provider is disabled")
        raise HTTPException(status_code=404)

    oidc_client = _oidc_oauth_client()

    try:
        token = await oidc_client.authorize_access_token(request)
    except OAuthError:
        if debug_oidc:
            logger.info("OIDC callback OAuthError during token/id token exchange")
        return RedirectResponse(url="/login", status_code=303)

    token_keys: list[str] = [str(key) for key in token]
    if debug_oidc:
        logger.info("OIDC callback token keys=%s", sorted(token_keys))

    parsed_userinfo: Mapping[str, object] | None = None
    if "id_token" in token:
        try:
            parsed_userinfo = _as_userinfo_mapping(await oidc_client.parse_id_token(request, token))
        except TypeError:
            parsed_userinfo = _as_userinfo_mapping(
                await oidc_client.parse_id_token(token, nonce=None)
            )
        except Exception:
            if debug_oidc:
                logger.info(
                    "OIDC callback parse_id_token failed; falling back to userinfo endpoint"
                )

    if parsed_userinfo is None:
        parsed_userinfo = _as_userinfo_mapping(await oidc_client.userinfo(token=token))

    userinfo = parsed_userinfo

    sub = str(userinfo.get("sub", ""))
    email = str(userinfo.get("email", "")).strip().lower()
    email_verified = _claim_is_truthy(userinfo.get("email_verified"))

    if debug_oidc:
        logger.info(
            "OIDC callback claims parsed: sub_present=%s email=%s email_verified=%s claim_keys=%s",
            bool(sub),
            email,
            email_verified,
            sorted(userinfo.keys()),
        )

    if not sub or not email or not email_verified:
        if debug_oidc:
            logger.info(
                "OIDC callback rejected claims: sub_present=%s email_present=%s email_verified=%s",
                bool(sub),
                bool(email),
                email_verified,
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
            return _render_login_template(
                request,
                error="Email domain not allowed",
                status_code=403,
            )

    ext_repo = ExternalIdentityRepository(session)
    ext = await ext_repo.get_by_provider_subject("oidc", sub)

    if ext is not None:
        if debug_oidc:
            logger.info("OIDC callback found external identity for subject: %s", sub)
        users = UserRepository(session)
        user = await users.get_by_id(ext.user_id)
        if user is None:
            if debug_oidc:
                logger.info("OIDC callback identity points to missing user id=%s", ext.user_id)
            return RedirectResponse(url="/login", status_code=303)
        if not user.is_active:
            if debug_oidc:
                logger.info("OIDC callback denied inactive user for external identity: %s", user.id)
            return _render_login_template(
                request,
                error="This account is deactivated. Contact an admin.",
                status_code=403,
            )
    else:
        if debug_oidc:
            logger.info("OIDC callback no external identity for subject: %s", sub)
        users = UserRepository(session)
        user = await users.get_by_email(email)
        if user is None:
            if debug_oidc:
                logger.info("OIDC callback auto-provisioning new user for email=%s", email)
            user = await _provision_user_for_oidc(session, email=email, userinfo=userinfo)
        elif not user.is_active:
            if debug_oidc:
                logger.info("OIDC callback denied inactive email match for email=%s", email)
            return _render_login_template(
                request,
                error="This account is deactivated. Contact an admin.",
                status_code=403,
            )
        elif user.password_hash is not None:
            if debug_oidc:
                logger.info(
                    "OIDC callback denied linking password-based account for email=%s",
                    email,
                )
            return _render_login_template(
                request,
                error="An account with this email already exists. Sign in with password first to link SSO.",
                status_code=403,
            )
        elif debug_oidc:
            logger.info("OIDC callback linking existing SSO account for email=%s", email)

        ext = ExternalIdentity(user_id=user.id, provider="oidc", subject=sub, email=email)
        session.add(ext)
        await session.commit()

    if debug_oidc:
        logger.info("OIDC callback success user_id=%s email=%s", user.id, user.email)

    await _maybe_promote_bootstrap_admin(user, session)

    request.session["user_id"] = str(user.id)
    return RedirectResponse(url="/dashboard", status_code=303)


@router.post("/logout", response_class=RedirectResponse)
async def logout(request: Request) -> RedirectResponse:
    request.session.clear()
    return RedirectResponse(url="/", status_code=303)


@router.get("/profile", response_class=HTMLResponse)
async def profile(
    request: Request,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> HTMLResponse:
    user = await _get_user_or_404(session, current_user.id)

    return templates.TemplateResponse(
        request,
        "profile.html",
        {
            "user": user,
            "current_user": user,
        },
    )


@router.post("/profile", response_class=RedirectResponse)
async def update_profile(
    request: Request,
    first_name: str = Form(...),
    last_name: str = Form(""),
    timezone: str = Form("UTC"),
    prefers_dark_mode: str | None = Form(None),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(require_user),
) -> RedirectResponse:
    user = await _get_user_or_404(session, current_user.id)

    normalized_first_name = first_name.strip()
    normalized_last_name = last_name.strip()
    timezone_input = timezone.strip() or "UTC"
    normalized_timezone = parse_timezone(timezone_input).key
    if not normalized_first_name:
        raise HTTPException(status_code=400, detail="First name is required")

    user.first_name = normalized_first_name
    user.last_name = normalized_last_name
    user.timezone = normalized_timezone
    user.display_name = f"{normalized_first_name} {normalized_last_name}".strip()
    user.prefers_dark_mode = prefers_dark_mode is not None
    await session.commit()

    return RedirectResponse(url="/profile", status_code=303)

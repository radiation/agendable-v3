from __future__ import annotations

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
from agendable.sso_google import google_enabled
from agendable.web.routes.common import oauth, parse_timezone, templates

router = APIRouter()


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
            "google_enabled": google_enabled(),
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


def _google_oauth_client() -> Any:
    return cast(Any, oauth).google


def _as_userinfo_mapping(value: object) -> Mapping[str, object]:
    return cast(Mapping[str, object], value)


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


@router.get("/auth/google/start", response_class=RedirectResponse)
async def google_start(request: Request) -> Response:
    if not google_enabled():
        raise HTTPException(status_code=404)

    redirect_uri = str(request.url_for("google_callback"))
    google_client = _google_oauth_client()
    return cast(Response, await google_client.authorize_redirect(request, redirect_uri))


@router.get("/auth/google/callback", name="google_callback")
async def google_callback(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    if not google_enabled():
        raise HTTPException(status_code=404)

    google_client = _google_oauth_client()

    try:
        token = await google_client.authorize_access_token(request)
        userinfo = _as_userinfo_mapping(await google_client.parse_id_token(request, token))
    except OAuthError:
        return RedirectResponse(url="/login", status_code=303)

    sub = str(userinfo.get("sub", ""))
    email = str(userinfo.get("email", "")).strip().lower()
    email_verified = bool(userinfo.get("email_verified"))

    if not sub or not email or not email_verified:
        return RedirectResponse(url="/login", status_code=303)

    settings = get_settings()
    if settings.allowed_email_domain is not None:
        allowed = settings.allowed_email_domain.strip().lower().lstrip("@")
        if not email.endswith(f"@{allowed}"):
            return _render_login_template(
                request,
                error="Email domain not allowed",
                status_code=403,
            )

    ext_repo = ExternalIdentityRepository(session)
    ext = await ext_repo.get_by_provider_subject("google", sub)

    if ext is not None:
        users = UserRepository(session)
        user = await users.get_by_id(ext.user_id)
        if user is None:
            return RedirectResponse(url="/login", status_code=303)
    else:
        users = UserRepository(session)
        user = await users.get_by_email(email)
        if user is None:
            return _render_login_template(
                request,
                error="Account not found. Create one first.",
                status_code=403,
            )

        ext = ExternalIdentity(user_id=user.id, provider="google", subject=sub, email=email)
        session.add(ext)
        await session.commit()

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

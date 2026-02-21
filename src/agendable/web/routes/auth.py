from __future__ import annotations

from typing import cast

from authlib.integrations.starlette_client import OAuthError
from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.responses import Response

from agendable.auth import hash_password, require_user, verify_password
from agendable.db import get_session
from agendable.db.models import ExternalIdentity, User
from agendable.db.repos import ExternalIdentityRepository, UserRepository
from agendable.settings import get_settings
from agendable.sso_google import google_enabled
from agendable.web.routes.common import oauth, templates

router = APIRouter()


@router.get("/login", response_class=Response)
async def login_form(request: Request, session: AsyncSession = Depends(get_session)) -> Response:
    try:
        _ = await require_user(request, session)
        return RedirectResponse(url="/", status_code=303)
    except HTTPException:
        pass

    return templates.TemplateResponse(
        request,
        "login.html",
        {
            "error": None,
            "current_user": None,
            "google_enabled": google_enabled(),
        },
    )


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
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "error": "Account not found. Create one first.",
                "current_user": None,
                "google_enabled": google_enabled(),
            },
            status_code=401,
        )

    if user.password_hash is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            request,
            "login.html",
            {
                "error": "Invalid email or password",
                "current_user": None,
                "google_enabled": google_enabled(),
            },
            status_code=401,
        )

    request.session["user_id"] = str(user.id)
    return RedirectResponse(url="/", status_code=303)


@router.get("/signup", response_class=Response)
async def signup_form(request: Request, session: AsyncSession = Depends(get_session)) -> Response:
    try:
        _ = await require_user(request, session)
        return RedirectResponse(url="/", status_code=303)
    except HTTPException:
        pass

    return templates.TemplateResponse(
        request,
        "signup.html",
        {
            "error": None,
            "current_user": None,
        },
    )


@router.post("/signup", response_class=HTMLResponse)
async def signup(
    request: Request,
    email: str = Form(...),
    password: str = Form(...),
    session: AsyncSession = Depends(get_session),
) -> Response:
    normalized_email = email.strip().lower()
    if not normalized_email:
        raise HTTPException(status_code=400, detail="Email is required")

    users = UserRepository(session)
    existing = await users.get_by_email(normalized_email)
    if existing is not None:
        return templates.TemplateResponse(
            request,
            "signup.html",
            {
                "error": "Account already exists. Sign in instead.",
                "current_user": None,
            },
            status_code=400,
        )

    user = User(
        email=normalized_email,
        display_name=normalized_email,
        password_hash=hash_password(password),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    request.session["user_id"] = str(user.id)
    return RedirectResponse(url="/", status_code=303)


@router.get("/auth/google/start", response_class=RedirectResponse)
async def google_start(request: Request) -> Response:
    if not google_enabled():
        raise HTTPException(status_code=404)

    redirect_uri = str(request.url_for("google_callback"))
    return cast(Response, await oauth.google.authorize_redirect(request, redirect_uri))


@router.get("/auth/google/callback", name="google_callback")
async def google_callback(
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> Response:
    if not google_enabled():
        raise HTTPException(status_code=404)

    try:
        token = await oauth.google.authorize_access_token(request)
        userinfo = await oauth.google.parse_id_token(request, token)
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
            return templates.TemplateResponse(
                request,
                "login.html",
                {
                    "error": "Email domain not allowed",
                    "current_user": None,
                    "google_enabled": google_enabled(),
                },
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
            return templates.TemplateResponse(
                request,
                "login.html",
                {
                    "error": "Account not found. Create one first.",
                    "current_user": None,
                    "google_enabled": google_enabled(),
                },
                status_code=403,
            )

        ext = ExternalIdentity(user_id=user.id, provider="google", subject=sub, email=email)
        session.add(ext)
        await session.commit()

    request.session["user_id"] = str(user.id)
    return RedirectResponse(url="/", status_code=303)


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
    users = UserRepository(session)
    user = await users.get_by_id(current_user.id)
    if user is None:
        raise HTTPException(status_code=404)

    return templates.TemplateResponse(
        request,
        "profile.html",
        {
            "user": user,
            "current_user": user,
        },
    )

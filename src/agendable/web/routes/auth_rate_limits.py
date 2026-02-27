from __future__ import annotations

import uuid

from fastapi import Request

from agendable.rate_limit import RateLimitRule, consume_rate_limit, is_rate_limited
from agendable.settings import Settings, get_settings


def client_ip(request: Request) -> str:
    settings = get_settings()
    if settings.trust_proxy_headers:
        real_ip = request.headers.get("x-real-ip", "").strip()
        if real_ip:
            return real_ip

        forwarded = request.headers.get("x-forwarded-for", "")
        if forwarded:
            forwarded_ip = forwarded.split(",", 1)[0].strip()
            if forwarded_ip:
                return forwarded_ip

    if request.client is not None and request.client.host:
        return request.client.host
    return "unknown"


def is_login_rate_limited(request: Request, email: str) -> bool:
    settings = get_settings()
    if not settings.auth_rate_limit_enabled:
        return False

    ip_limited = is_rate_limited(
        RateLimitRule(
            bucket="login-ip",
            max_attempts=settings.login_rate_limit_ip_attempts,
            window_seconds=settings.login_rate_limit_ip_window_seconds,
        ),
        client_ip(request),
    )
    account_limited = is_rate_limited(
        RateLimitRule(
            bucket="login-account",
            max_attempts=settings.login_rate_limit_account_attempts,
            window_seconds=settings.login_rate_limit_account_window_seconds,
        ),
        email,
    )
    return ip_limited or account_limited


def record_login_failure(request: Request, email: str) -> None:
    settings = get_settings()
    if not settings.auth_rate_limit_enabled:
        return

    _ = consume_rate_limit(
        RateLimitRule(
            bucket="login-ip",
            max_attempts=settings.login_rate_limit_ip_attempts,
            window_seconds=settings.login_rate_limit_ip_window_seconds,
        ),
        client_ip(request),
    )
    _ = consume_rate_limit(
        RateLimitRule(
            bucket="login-account",
            max_attempts=settings.login_rate_limit_account_attempts,
            window_seconds=settings.login_rate_limit_account_window_seconds,
        ),
        email,
    )


def is_oidc_callback_rate_limited(
    request: Request,
    *,
    settings: Settings,
    account_key: str,
) -> bool:
    if not settings.auth_rate_limit_enabled:
        return False

    ip_limited = consume_rate_limit(
        RateLimitRule(
            bucket="oidc-callback-ip",
            max_attempts=settings.oidc_callback_rate_limit_ip_attempts,
            window_seconds=settings.oidc_callback_rate_limit_ip_window_seconds,
        ),
        client_ip(request),
    )
    account_limited = consume_rate_limit(
        RateLimitRule(
            bucket="oidc-callback-account",
            max_attempts=settings.oidc_callback_rate_limit_account_attempts,
            window_seconds=settings.oidc_callback_rate_limit_account_window_seconds,
        ),
        account_key,
    )
    return ip_limited or account_limited


def is_identity_link_start_rate_limited(
    request: Request,
    *,
    user_id: uuid.UUID,
) -> bool:
    settings = get_settings()
    if not settings.auth_rate_limit_enabled:
        return False

    ip_limited = consume_rate_limit(
        RateLimitRule(
            bucket="identity-link-start-ip",
            max_attempts=settings.identity_link_start_rate_limit_ip_attempts,
            window_seconds=settings.identity_link_start_rate_limit_ip_window_seconds,
        ),
        client_ip(request),
    )
    account_limited = consume_rate_limit(
        RateLimitRule(
            bucket="identity-link-start-account",
            max_attempts=settings.identity_link_start_rate_limit_account_attempts,
            window_seconds=settings.identity_link_start_rate_limit_account_window_seconds,
        ),
        str(user_id),
    )
    return ip_limited or account_limited

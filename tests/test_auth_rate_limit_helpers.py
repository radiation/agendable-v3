from __future__ import annotations

import uuid

import pytest
from starlette.requests import Request

from agendable.rate_limit import RateLimitRule, consume_rate_limit
from agendable.services.oidc_service import is_email_allowed_for_domain, oidc_login_error_message
from agendable.settings import Settings
from agendable.web.routes.auth_rate_limits import (
    client_ip,
    is_identity_link_start_rate_limited,
    is_oidc_callback_rate_limited,
)


def _build_request(
    *, forwarded_for: str | None = None, client_host: str | None = "127.0.0.1"
) -> Request:
    headers: list[tuple[bytes, bytes]] = []
    if forwarded_for is not None:
        headers.append((b"x-forwarded-for", forwarded_for.encode("utf-8")))

    scope: dict[str, object] = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": headers,
    }
    if client_host is not None:
        scope["client"] = (client_host, 12345)
    return Request(scope)


def test_consume_rate_limit_handles_invalid_rule_values() -> None:
    invalid_attempts = RateLimitRule(bucket="invalid-a", max_attempts=0, window_seconds=60)
    invalid_window = RateLimitRule(bucket="invalid-w", max_attempts=1, window_seconds=0)

    assert consume_rate_limit(invalid_attempts, "k") is False
    assert consume_rate_limit(invalid_window, "k") is False


def test_consume_rate_limit_blocks_when_threshold_reached() -> None:
    rule = RateLimitRule(bucket="threshold", max_attempts=2, window_seconds=60)

    assert consume_rate_limit(rule, "user") is False
    assert consume_rate_limit(rule, "user") is False
    assert consume_rate_limit(rule, "user") is True


def test_client_ip_prefers_forwarded_then_client_then_unknown() -> None:
    forwarded = _build_request(forwarded_for="203.0.113.10, 10.0.0.2", client_host="127.0.0.1")
    from_client = _build_request(forwarded_for=None, client_host="127.0.0.2")
    unknown = _build_request(forwarded_for="", client_host=None)

    assert client_ip(forwarded) == "203.0.113.10"
    assert client_ip(from_client) == "127.0.0.2"
    assert client_ip(unknown) == "unknown"


def test_oidc_callback_rate_limit_respects_disabled_setting() -> None:
    request = _build_request()
    settings = Settings(auth_rate_limit_enabled=False)

    assert is_oidc_callback_rate_limited(request, settings=settings, account_key="account") is False


def test_identity_link_start_rate_limit_respects_disabled_setting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENDABLE_AUTH_RATE_LIMIT_ENABLED", "false")
    request = _build_request()

    assert is_identity_link_start_rate_limited(request, user_id=uuid.uuid4()) is False


def test_oidc_service_helper_functions_cover_known_and_unknown_cases() -> None:
    assert oidc_login_error_message("inactive_user") is not None
    assert oidc_login_error_message("password_user_requires_link") is not None
    assert oidc_login_error_message("other") is None

    assert is_email_allowed_for_domain("alice@example.com", None) is True
    assert is_email_allowed_for_domain("alice@example.com", " @example.com ") is True
    assert is_email_allowed_for_domain("alice@other.com", "example.com") is False

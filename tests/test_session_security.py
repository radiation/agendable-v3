from __future__ import annotations

import pytest

from agendable.app import create_app


def _session_middleware_kwargs() -> dict[str, object]:
    app = create_app()
    middleware = next(
        layer
        for layer in app.user_middleware
        if getattr(layer.cls, "__name__", "") == "SessionMiddleware"
    )
    return middleware.kwargs


def test_session_cookie_hardening_settings_are_applied(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENDABLE_SESSION_COOKIE_SAME_SITE", "strict")
    monkeypatch.setenv("AGENDABLE_SESSION_COOKIE_HTTPS_ONLY", "true")
    monkeypatch.setenv("AGENDABLE_SESSION_COOKIE_MAX_AGE_SECONDS", "3600")
    monkeypatch.setenv("AGENDABLE_SESSION_COOKIE_NAME", "agendable_test_session")

    kwargs = _session_middleware_kwargs()

    assert kwargs["session_cookie"] == "agendable_test_session"
    assert kwargs["same_site"] == "strict"
    assert kwargs["https_only"] is True
    assert kwargs["max_age"] == 3600


def test_session_cookie_defaults_are_set_for_local_dev(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AGENDABLE_SESSION_COOKIE_SAME_SITE", raising=False)
    monkeypatch.delenv("AGENDABLE_SESSION_COOKIE_HTTPS_ONLY", raising=False)
    monkeypatch.delenv("AGENDABLE_SESSION_COOKIE_MAX_AGE_SECONDS", raising=False)
    monkeypatch.delenv("AGENDABLE_SESSION_COOKIE_NAME", raising=False)

    kwargs = _session_middleware_kwargs()

    assert kwargs["session_cookie"] == "agendable_session"
    assert kwargs["same_site"] == "lax"
    assert kwargs["https_only"] is False
    assert kwargs["max_age"] == 60 * 60 * 24 * 14

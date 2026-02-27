from __future__ import annotations

import logging

import pytest

from agendable.logging_config import configure_logging, format_log_fields, log_security_audit_event
from agendable.settings import Settings


def test_format_log_fields_escapes_control_characters() -> None:
    fields = format_log_fields(actor_email="evil@example.com\nforged", note="a\rb\tc\x00")

    assert "actor_email=evil@example.com\\nforged" in fields
    assert "note=a\\rb\\tc\\x00" in fields
    assert "\n" not in fields.replace("\\n", "")
    assert "\r" not in fields.replace("\\r", "")


def test_log_security_audit_event_sanitizes_user_controlled_values(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.INFO, logger="agendable.security.audit")
    log_security_audit_event(
        audit_event="auth.password_login",
        outcome="denied",
        actor_email="user@example.com\nforged_line",
        requested_role="admin\rroot",
    )

    messages = [record.getMessage() for record in caplog.records]
    assert len(messages) == 1

    message = messages[0]
    assert "actor_email=user@example.com\\nforged_line" in message
    assert "requested_role=admin\\rroot" in message
    assert "\n" not in message
    assert "\r" not in message


def test_configure_logging_keeps_security_audit_logger_at_info() -> None:
    configure_logging(Settings(log_level="WARNING"))

    audit_logger = logging.getLogger("agendable.security.audit")
    assert audit_logger.getEffectiveLevel() == logging.INFO

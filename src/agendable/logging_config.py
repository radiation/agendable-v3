from __future__ import annotations

import json
import logging
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from logging.config import dictConfig
from typing import Any

from agendable.settings import Settings, get_settings

_VALID_LOG_LEVELS = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
_request_id_context: ContextVar[str | None] = ContextVar("agendable_request_id", default=None)


def normalize_log_level(raw_level: str) -> str:
    level = raw_level.strip().upper()
    if level in _VALID_LOG_LEVELS:
        return level
    return "INFO"


class JsonLogFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "request_id": getattr(record, "request_id", "-"),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=True)


def get_request_id() -> str | None:
    return _request_id_context.get()


def set_request_id(request_id: str) -> Token[str | None]:
    return _request_id_context.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    _request_id_context.reset(token)


class RequestContextFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        request_id = get_request_id()
        record.request_id = request_id if request_id else "-"
        return True


def _format_log_value(value: object) -> str:
    if isinstance(value, str):
        return value
    return str(value)


def format_log_fields(**fields: object) -> str:
    parts: list[str] = []
    for key in sorted(fields):
        value = fields[key]
        if value is None:
            continue
        parts.append(f"{key}={_format_log_value(value)}")
    return " ".join(parts)


def log_with_fields(
    logger: logging.Logger,
    level: int,
    message: str,
    *,
    exc_info: Any | None = None,
    **fields: object,
) -> None:
    field_text = format_log_fields(**fields)
    if field_text:
        logger.log(level, "%s %s", message, field_text, exc_info=exc_info)
        return
    logger.log(level, "%s", message, exc_info=exc_info)


def log_security_audit_event(
    *,
    audit_event: str,
    outcome: str,
    **fields: object,
) -> None:
    event_logger = logging.getLogger("agendable.security.audit")
    selected_level = logging.INFO
    provided_level = fields.get("audit_level")
    if isinstance(provided_level, int):
        selected_level = provided_level

    sanitized_fields = dict(fields)
    sanitized_fields.pop("audit_level", None)

    log_with_fields(
        event_logger,
        selected_level,
        "security audit event",
        audit_event=audit_event,
        outcome=outcome,
        **sanitized_fields,
    )


def configure_logging(settings: Settings | None = None) -> None:
    selected_settings = settings if settings is not None else get_settings()
    level = normalize_log_level(selected_settings.log_level)
    formatter_name = "json" if selected_settings.log_json else "plain"

    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "filters": {
                "request_context": {
                    "()": "agendable.logging_config.RequestContextFilter",
                }
            },
            "formatters": {
                "plain": {
                    "format": "%(asctime)s %(levelname)s [%(name)s] [req=%(request_id)s] %(message)s",
                },
                "json": {
                    "()": "agendable.logging_config.JsonLogFormatter",
                },
            },
            "handlers": {
                "default": {
                    "class": "logging.StreamHandler",
                    "filters": ["request_context"],
                    "formatter": formatter_name,
                }
            },
            "root": {"handlers": ["default"], "level": level},
            "loggers": {
                "uvicorn": {"level": level},
                "uvicorn.error": {"level": level},
                "uvicorn.access": {"level": level},
            },
        }
    )

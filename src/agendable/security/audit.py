from __future__ import annotations

import logging
from collections.abc import Mapping

from agendable.db.models import User
from agendable.logging_config import log_security_audit_event


def _actor_fields(
    *,
    actor: User | None = None,
    actor_user_id: object | None = None,
    actor_email: str | None = None,
) -> dict[str, object]:
    fields: dict[str, object] = {}

    resolved_user_id: object | None = actor_user_id
    resolved_email: str | None = actor_email

    if actor is not None:
        if resolved_user_id is None:
            resolved_user_id = actor.id
        if resolved_email is None:
            resolved_email = actor.email

    if resolved_user_id is not None:
        fields["actor_user_id"] = resolved_user_id
    if resolved_email is not None:
        fields["actor_email"] = resolved_email

    return fields


def _emit_security_audit(
    *,
    namespace: str,
    event: str,
    outcome: str,
    audit_level: int = logging.INFO,
    reason: str | None = None,
    actor: User | None = None,
    actor_user_id: object | None = None,
    actor_email: str | None = None,
    fields: Mapping[str, object] | None = None,
) -> None:
    payload: dict[str, object] = {}
    payload.update(_actor_fields(actor=actor, actor_user_id=actor_user_id, actor_email=actor_email))
    if fields:
        payload.update(fields)

    if reason is not None:
        payload["reason"] = reason

    log_security_audit_event(
        audit_event=f"{namespace}.{event}",
        outcome=outcome,
        audit_level=audit_level,
        **payload,
    )


def audit_auth_denied(
    *,
    event: str,
    reason: str,
    actor: User | None = None,
    actor_user_id: object | None = None,
    actor_email: str | None = None,
    **fields: object,
) -> None:
    _emit_security_audit(
        namespace="auth",
        event=event,
        outcome="denied",
        audit_level=logging.WARNING,
        reason=reason,
        actor=actor,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        fields=fields,
    )


def audit_auth_success(
    *,
    event: str,
    actor: User | None = None,
    actor_user_id: object | None = None,
    actor_email: str | None = None,
    **fields: object,
) -> None:
    _emit_security_audit(
        namespace="auth",
        event=event,
        outcome="success",
        actor=actor,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        fields=fields,
    )


def audit_oidc_denied(
    *,
    event: str,
    reason: str,
    actor: User | None = None,
    actor_user_id: object | None = None,
    actor_email: str | None = None,
    **fields: object,
) -> None:
    _emit_security_audit(
        namespace="auth.oidc",
        event=event,
        outcome="denied",
        audit_level=logging.WARNING,
        reason=reason,
        actor=actor,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        fields=fields,
    )


def audit_oidc_success(
    *,
    event: str,
    actor: User | None = None,
    actor_user_id: object | None = None,
    actor_email: str | None = None,
    **fields: object,
) -> None:
    _emit_security_audit(
        namespace="auth.oidc",
        event=event,
        outcome="success",
        actor=actor,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        fields=fields,
    )


def audit_admin_denied(
    *,
    event: str,
    reason: str,
    actor: User | None = None,
    actor_user_id: object | None = None,
    actor_email: str | None = None,
    **fields: object,
) -> None:
    _emit_security_audit(
        namespace="admin",
        event=event,
        outcome="denied",
        audit_level=logging.WARNING,
        reason=reason,
        actor=actor,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        fields=fields,
    )


def audit_admin_success(
    *,
    event: str,
    actor: User | None = None,
    actor_user_id: object | None = None,
    actor_email: str | None = None,
    **fields: object,
) -> None:
    _emit_security_audit(
        namespace="admin",
        event=event,
        outcome="success",
        actor=actor,
        actor_user_id=actor_user_id,
        actor_email=actor_email,
        fields=fields,
    )

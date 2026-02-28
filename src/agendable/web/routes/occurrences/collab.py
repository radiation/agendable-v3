from __future__ import annotations

import uuid
from datetime import UTC, datetime

from agendable.db.models import AgendaItem, MeetingOccurrence, Task

PRESENCE_WINDOW_SECONDS = 30

_occurrence_presence: dict[uuid.UUID, dict[uuid.UUID, datetime]] = {}
_occurrence_last_activity: dict[uuid.UUID, tuple[datetime, str]] = {}


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def relative_time_label(*, then: datetime, now: datetime) -> str:
    normalized_then = as_utc(then)
    normalized_now = as_utc(now)
    delta_seconds = max(0, int((normalized_now - normalized_then).total_seconds()))
    if delta_seconds < 5:
        return "just now"
    if delta_seconds < 60:
        return f"{delta_seconds}s ago"
    if delta_seconds < 3600:
        return f"{delta_seconds // 60}m ago"
    return f"{delta_seconds // 3600}h ago"


def mark_presence(*, occurrence_id: uuid.UUID, user_id: uuid.UUID, now: datetime) -> int:
    occurrence_presence = _occurrence_presence.setdefault(occurrence_id, {})
    cutoff = now.timestamp() - PRESENCE_WINDOW_SECONDS
    stale_user_ids = [
        seen_user_id
        for seen_user_id, seen_at in occurrence_presence.items()
        if seen_at.timestamp() < cutoff
    ]
    for stale_user_id in stale_user_ids:
        occurrence_presence.pop(stale_user_id, None)
    occurrence_presence[user_id] = now
    return len(occurrence_presence)


def record_occurrence_activity(
    *,
    occurrence_id: uuid.UUID,
    actor_display_name: str,
    now: datetime,
) -> None:
    _occurrence_last_activity[occurrence_id] = (as_utc(now), actor_display_name)


def latest_content_activity_at(
    *,
    occurrence: MeetingOccurrence,
    tasks: list[Task],
    agenda_items: list[AgendaItem],
) -> datetime:
    latest = as_utc(occurrence.created_at)
    for task in tasks:
        task_created_at = as_utc(task.created_at)
        if task_created_at > latest:
            latest = task_created_at
    for agenda_item in agenda_items:
        agenda_created_at = as_utc(agenda_item.created_at)
        if agenda_created_at > latest:
            latest = agenda_created_at
    return latest


def get_tracked_occurrence_activity(
    occurrence_id: uuid.UUID,
) -> tuple[datetime, str] | None:
    return _occurrence_last_activity.get(occurrence_id)

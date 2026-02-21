"""Repository layer.

These repositories encapsulate common query patterns for the app's entities.
Keep them focused on persistence/query shaping; business logic lives in services.
"""

from agendable.db.repos.agenda_items import AgendaItemRepository
from agendable.db.repos.external_identities import ExternalIdentityRepository
from agendable.db.repos.meeting_occurrences import MeetingOccurrenceRepository
from agendable.db.repos.meeting_series import MeetingSeriesRepository
from agendable.db.repos.tasks import TaskRepository
from agendable.db.repos.users import UserRepository

__all__ = [
    "AgendaItemRepository",
    "ExternalIdentityRepository",
    "MeetingOccurrenceRepository",
    "MeetingSeriesRepository",
    "TaskRepository",
    "UserRepository",
]

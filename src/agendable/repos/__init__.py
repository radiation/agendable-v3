"""Repository layer.

These repositories encapsulate common query patterns for the app's entities.
Keep them focused on persistence/query shaping; business logic lives in services.
"""

from agendable.repos.agenda_items import AgendaItemRepository
from agendable.repos.external_identities import ExternalIdentityRepository
from agendable.repos.meeting_occurrences import MeetingOccurrenceRepository
from agendable.repos.meeting_series import MeetingSeriesRepository
from agendable.repos.tasks import TaskRepository
from agendable.repos.users import UserRepository

__all__ = [
    "AgendaItemRepository",
    "ExternalIdentityRepository",
    "MeetingOccurrenceRepository",
    "MeetingSeriesRepository",
    "TaskRepository",
    "UserRepository",
]

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from agendable.db.models import Reminder, ReminderChannel, ReminderDeliveryStatus
from agendable.db.repos import ReminderRepository
from agendable.logging_config import log_with_fields
from agendable.reminders import ReminderDeliveryError, ReminderEmail, ReminderSender, as_utc
from agendable.services.reminder_claim_service import (
    claim_reminder_attempt as claim_reminder_attempt_in_service,
)
from agendable.settings import Settings, get_settings


@dataclass(slots=True)
class ReminderRunStats:
    attempted: int = 0
    sent: int = 0
    failed: int = 0
    skipped: int = 0
    retried: int = 0
    failure_reason_counts: Counter[str] = field(default_factory=Counter)


type ClaimAttemptFn = Callable[..., Awaitable[bool]]


def _is_reminder_due(reminder: Reminder, now: datetime) -> bool:
    next_attempt_at = reminder.next_attempt_at if reminder.next_attempt_at else reminder.send_at
    return as_utc(next_attempt_at) <= now


def _build_reminder_email(reminder: Reminder) -> ReminderEmail:
    return ReminderEmail(
        recipient_email=reminder.occurrence.series.owner.email,
        meeting_title=reminder.occurrence.series.title,
        scheduled_at=as_utc(reminder.occurrence.scheduled_at),
        incomplete_tasks=[task.title for task in reminder.occurrence.tasks if not task.is_done],
    )


def _log_run_summary(
    *, logger: logging.Logger, started_at: datetime, stats: ReminderRunStats
) -> None:
    duration_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
    log_with_fields(
        logger,
        logging.INFO,
        "reminder run complete",
        attempted=stats.attempted,
        sent=stats.sent,
        failed=stats.failed,
        skipped=stats.skipped,
        retried=stats.retried,
        duration_ms=duration_ms,
    )

    for reason_code, count in sorted(stats.failure_reason_counts.items()):
        log_with_fields(
            logger,
            logging.INFO,
            "reminder run failure reason",
            reason_code=reason_code,
            count=count,
        )


class ReminderDeliveryService:
    def __init__(
        self,
        *,
        reminder_repo: ReminderRepository,
        sender: ReminderSender,
        logger: logging.Logger,
        settings: Settings | None = None,
        claim_attempt: ClaimAttemptFn | None = None,
    ) -> None:
        self.reminder_repo = reminder_repo
        self.sender = sender
        self.logger = logger
        self.settings = settings if settings is not None else get_settings()
        self.claim_attempt = claim_attempt

    async def run_due_reminders(self) -> None:
        started_at = datetime.now(UTC)
        now = started_at
        stats = ReminderRunStats()

        reminders = await self.reminder_repo.list_pending_for_delivery()
        for reminder in reminders:
            if not _is_reminder_due(reminder, now):
                continue

            if reminder.channel != ReminderChannel.email:
                reminder.delivery_status = ReminderDeliveryStatus.skipped
                reminder.failure_reason_code = "unsupported_channel"
                stats.skipped += 1
                continue

            was_claimed = await self._claim_attempt(reminder=reminder, now=now)
            if not was_claimed:
                continue

            stats.attempted += 1

            try:
                await self.sender.send_email_reminder(_build_reminder_email(reminder))
            except ReminderDeliveryError as exc:
                reminder.failure_reason_code = exc.reason_code
                stats.failure_reason_counts[exc.reason_code] += 1
                if (
                    exc.is_transient
                    and reminder.attempt_count < self.settings.reminder_retry_max_attempts
                ):
                    backoff_seconds = self.settings.reminder_retry_backoff_seconds * (
                        2 ** (reminder.attempt_count - 1)
                    )
                    reminder.next_attempt_at = now + timedelta(seconds=backoff_seconds)
                    reminder.delivery_status = ReminderDeliveryStatus.retry_scheduled
                    stats.retried += 1
                    log_with_fields(
                        self.logger,
                        logging.WARNING,
                        "reminder delivery transient failure",
                        reminder_id=reminder.id,
                        reason_code=exc.reason_code,
                        attempt_count=reminder.attempt_count,
                        next_attempt_at=reminder.next_attempt_at.isoformat(),
                        backoff_seconds=backoff_seconds,
                    )
                else:
                    reminder.delivery_status = ReminderDeliveryStatus.failed_terminal
                    stats.failed += 1
                    log_with_fields(
                        self.logger,
                        logging.ERROR,
                        "reminder delivery terminal failure",
                        reminder_id=reminder.id,
                        reason_code=exc.reason_code,
                        attempt_count=reminder.attempt_count,
                    )
                continue

            reminder.sent_at = now
            reminder.next_attempt_at = now
            reminder.delivery_status = ReminderDeliveryStatus.sent
            reminder.failure_reason_code = None
            stats.sent += 1

        await self.reminder_repo.commit()
        _log_run_summary(logger=self.logger, started_at=started_at, stats=stats)

    async def _claim_attempt(self, *, reminder: Reminder, now: datetime) -> bool:
        if self.claim_attempt is not None:
            return await self.claim_attempt(reminder=reminder, now=now)

        return await claim_reminder_attempt_in_service(
            reminder=reminder,
            now=now,
            claim_lease_seconds=self.settings.reminder_claim_lease_seconds,
        )


async def run_due_reminders(
    *,
    reminder_repo: ReminderRepository,
    sender: ReminderSender,
    logger: logging.Logger,
    settings: Settings | None = None,
    claim_attempt: ClaimAttemptFn | None = None,
) -> None:
    service = ReminderDeliveryService(
        reminder_repo=reminder_repo,
        sender=sender,
        logger=logger,
        settings=settings,
        claim_attempt=claim_attempt,
    )
    await service.run_due_reminders()

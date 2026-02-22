from __future__ import annotations

import asyncio
import smtplib
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from typing import Protocol

from agendable.db.models import Reminder, ReminderChannel
from agendable.settings import Settings


@dataclass(slots=True)
class ReminderEmail:
    recipient_email: str
    meeting_title: str
    scheduled_at: datetime
    incomplete_tasks: list[str] = field(default_factory=list)


class ReminderSender(Protocol):
    async def send_email_reminder(self, reminder: ReminderEmail) -> None: ...


def as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def build_default_email_reminder(
    occurrence_id: uuid.UUID,
    occurrence_scheduled_at: datetime,
    settings: Settings,
    lead_minutes_before: int | None = None,
) -> Reminder:
    configured_minutes = (
        settings.default_email_reminder_minutes_before
        if lead_minutes_before is None
        else lead_minutes_before
    )
    lead_minutes = max(configured_minutes, 0)
    scheduled_at_utc = as_utc(occurrence_scheduled_at)
    send_at = scheduled_at_utc - timedelta(minutes=lead_minutes)
    return Reminder(
        occurrence_id=occurrence_id,
        channel=ReminderChannel.email,
        send_at=send_at,
        sent_at=None,
    )


class NoopReminderSender:
    async def send_email_reminder(self, reminder: ReminderEmail) -> None:
        _ = reminder


class SmtpReminderSender:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        from_email: str,
        username: str | None,
        password: str | None,
        use_ssl: bool,
        use_starttls: bool,
        timeout_seconds: float,
    ) -> None:
        self.host = host
        self.port = port
        self.from_email = from_email
        self.username = username
        self.password = password
        self.use_ssl = use_ssl
        self.use_starttls = use_starttls
        self.timeout_seconds = timeout_seconds

    async def send_email_reminder(self, reminder: ReminderEmail) -> None:
        await asyncio.to_thread(self._send_sync, reminder)

    def _send_sync(self, reminder: ReminderEmail) -> None:
        message = EmailMessage()
        message["Subject"] = f"Reminder: {reminder.meeting_title}"
        message["From"] = self.from_email
        message["To"] = reminder.recipient_email
        body_lines = [
            f"Reminder for: {reminder.meeting_title}",
            f"Scheduled at: {reminder.scheduled_at.isoformat()}",
            "",
            "Incomplete tasks:",
        ]
        if reminder.incomplete_tasks:
            body_lines.extend([f"- {task_title}" for task_title in reminder.incomplete_tasks])
        else:
            body_lines.append("- None")

        message.set_content("\n".join(body_lines))

        if self.use_ssl:
            with smtplib.SMTP_SSL(self.host, self.port, timeout=self.timeout_seconds) as smtp:
                self._login_if_configured(smtp)
                smtp.send_message(message)
            return

        with smtplib.SMTP(self.host, self.port, timeout=self.timeout_seconds) as smtp:
            if self.use_starttls:
                smtp.starttls()
            self._login_if_configured(smtp)
            smtp.send_message(message)

    def _login_if_configured(self, smtp: smtplib.SMTP) -> None:
        if self.username is None:
            return
        if self.password is None:
            return
        smtp.login(self.username, self.password)


def build_reminder_sender(settings: Settings) -> ReminderSender:
    if settings.smtp_host is None or settings.smtp_from_email is None:
        return NoopReminderSender()

    return SmtpReminderSender(
        host=settings.smtp_host,
        port=settings.smtp_port,
        from_email=settings.smtp_from_email,
        username=settings.smtp_username,
        password=(
            settings.smtp_password.get_secret_value()
            if settings.smtp_password is not None
            else None
        ),
        use_ssl=settings.smtp_use_ssl,
        use_starttls=settings.smtp_use_starttls,
        timeout_seconds=settings.smtp_timeout_seconds,
    )

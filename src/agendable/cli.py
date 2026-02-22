from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

import agendable.db as db
from agendable.db.models import Base, MeetingOccurrence, MeetingSeries, Reminder, ReminderChannel
from agendable.reminders import ReminderEmail, ReminderSender, build_reminder_sender
from agendable.settings import get_settings


def _as_utc(dt: datetime) -> datetime:
    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


async def _init_db() -> None:
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _run_due_reminders(sender: ReminderSender | None = None) -> None:
    selected_sender = sender if sender is not None else build_reminder_sender(get_settings())
    now = datetime.now(UTC)
    async with db.SessionMaker() as session:
        result = await session.execute(
            select(Reminder)
            .options(
                selectinload(Reminder.occurrence)
                .selectinload(MeetingOccurrence.series)
                .selectinload(MeetingSeries.owner)
            )
            .where(Reminder.sent_at.is_(None))
            .order_by(Reminder.send_at.asc())
        )
        reminders = list(result.scalars().all())

        sent = 0
        skipped = 0
        for reminder in reminders:
            if _as_utc(reminder.send_at) > now:
                continue

            if reminder.channel != ReminderChannel.email:
                skipped += 1
                continue

            await selected_sender.send_email_reminder(
                ReminderEmail(
                    recipient_email=reminder.occurrence.series.owner.email,
                    meeting_title=reminder.occurrence.series.title,
                    scheduled_at=_as_utc(reminder.occurrence.scheduled_at),
                )
            )
            reminder.sent_at = now
            sent += 1

        await session.commit()

    print(f"Sent {sent} reminders; skipped {skipped} reminders")


def main() -> None:
    parser = argparse.ArgumentParser(prog="agendable")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("init-db")
    sub.add_parser("run-reminders")

    args = parser.parse_args()

    if args.cmd == "init-db":
        asyncio.run(_init_db())
    elif args.cmd == "run-reminders":
        asyncio.run(_run_due_reminders())
    else:
        raise SystemExit(2)

from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import selectinload

import agendable.db as db
from agendable.db.models import Base, MeetingOccurrence, MeetingSeries, Reminder, ReminderChannel
from agendable.reminders import ReminderEmail, ReminderSender, as_utc, build_reminder_sender
from agendable.settings import get_settings


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
                .selectinload(MeetingSeries.owner),
                selectinload(Reminder.occurrence).selectinload(MeetingOccurrence.tasks),
            )
            .where(Reminder.sent_at.is_(None))
            .order_by(Reminder.send_at.asc())
        )
        reminders = list(result.scalars().all())

        sent = 0
        skipped = 0
        for reminder in reminders:
            if as_utc(reminder.send_at) > now:
                continue

            if reminder.channel != ReminderChannel.email:
                skipped += 1
                continue

            await selected_sender.send_email_reminder(
                ReminderEmail(
                    recipient_email=reminder.occurrence.series.owner.email,
                    meeting_title=reminder.occurrence.series.title,
                    scheduled_at=as_utc(reminder.occurrence.scheduled_at),
                    incomplete_tasks=[
                        task.title for task in reminder.occurrence.tasks if not task.is_done
                    ],
                )
            )
            reminder.sent_at = now
            sent += 1

        await session.commit()

    print(f"Sent {sent} reminders; skipped {skipped} reminders")


async def _run_reminders_worker(poll_seconds: int) -> None:
    while True:
        await _run_due_reminders()
        await asyncio.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(prog="agendable")
    sub = parser.add_subparsers(dest="cmd", required=True)
    settings = get_settings()

    sub.add_parser("init-db")
    sub.add_parser("run-reminders")
    worker = sub.add_parser("run-reminders-worker")
    worker.add_argument(
        "--poll-seconds",
        type=int,
        default=settings.reminder_worker_poll_seconds,
    )

    args = parser.parse_args()

    if args.cmd == "init-db":
        asyncio.run(_init_db())
    elif args.cmd == "run-reminders":
        asyncio.run(_run_due_reminders())
    elif args.cmd == "run-reminders-worker":
        poll_seconds = max(1, int(args.poll_seconds))
        asyncio.run(_run_reminders_worker(poll_seconds))
    else:
        raise SystemExit(2)

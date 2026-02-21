from __future__ import annotations

import argparse
import asyncio
from datetime import UTC, datetime

from sqlalchemy import select

from agendable.db import SessionMaker, engine
from agendable.models import Base, Reminder


async def _init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _run_due_reminders() -> None:
    # TODO: Stub implementation marks due reminders as sent
    now = datetime.now(UTC)
    async with SessionMaker() as session:
        result = await session.execute(
            select(Reminder).where(Reminder.sent_at.is_(None)).order_by(Reminder.send_at.asc())
        )
        reminders = list(result.scalars().all())

        sent = 0
        for reminder in reminders:
            if reminder.send_at <= now:
                # TODO: Add integration with an actual notification system (email, push, etc.)
                reminder.sent_at = now
                sent += 1

        await session.commit()

    print(f"Marked {sent} reminders as sent")


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

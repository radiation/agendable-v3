from __future__ import annotations

import argparse
import asyncio
import logging
from datetime import UTC, datetime

from sqlalchemy import text

import agendable.db as db
from agendable.db.models import Base, Reminder
from agendable.db.repos import (
    ExternalCalendarConnectionRepository,
    ExternalCalendarEventMirrorRepository,
    ReminderRepository,
)
from agendable.logging_config import configure_logging, log_with_fields
from agendable.reminders import ReminderSender, build_reminder_sender
from agendable.services.calendar_event_mapping_service import CalendarEventMappingService
from agendable.services.google_calendar_client import GoogleCalendarHttpClient
from agendable.services.google_calendar_sync_service import GoogleCalendarSyncService
from agendable.services.reminder_claim_service import (
    claim_reminder_attempt as claim_reminder_attempt_in_service,
)
from agendable.services.reminder_delivery_service import run_due_reminders
from agendable.settings import get_settings

logger = logging.getLogger(__name__)


async def _init_db() -> None:
    async with db.engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def _claim_reminder_attempt(*, reminder: Reminder, now: datetime) -> bool:
    settings = get_settings()
    return await claim_reminder_attempt_in_service(
        reminder=reminder,
        now=now,
        claim_lease_seconds=settings.reminder_claim_lease_seconds,
    )


async def _run_due_reminders(sender: ReminderSender | None = None) -> None:
    settings = get_settings()
    selected_sender = sender if sender is not None else build_reminder_sender(settings)
    async with db.SessionMaker() as session:
        reminder_repo = ReminderRepository(session)
        await run_due_reminders(
            reminder_repo=reminder_repo,
            sender=selected_sender,
            logger=logger,
            settings=settings,
            claim_attempt=_claim_reminder_attempt,
        )


async def _run_reminders_worker(poll_seconds: int) -> None:
    while True:
        started_at = datetime.now(UTC)
        try:
            await _run_due_reminders()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("reminders worker iteration failed")
        finally:
            duration_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
            log_with_fields(
                logger,
                logging.INFO,
                "reminders worker iteration complete",
                duration_ms=duration_ms,
            )
        await asyncio.sleep(poll_seconds)


async def _run_google_calendar_sync() -> int:
    settings = get_settings()
    if not settings.google_calendar_sync_enabled:
        logger.info("google calendar sync skipped: feature disabled")
        return 0

    async with db.SessionMaker() as session:
        sync_service = GoogleCalendarSyncService(
            connection_repo=ExternalCalendarConnectionRepository(session),
            event_mirror_repo=ExternalCalendarEventMirrorRepository(session),
            calendar_client=GoogleCalendarHttpClient(
                api_base_url=settings.google_calendar_api_base_url,
                initial_sync_days_back=settings.google_calendar_initial_sync_days_back,
            ),
            event_mapper=CalendarEventMappingService.from_session(session),
            settings=settings,
        )
        synced_event_count = await sync_service.sync_all_enabled_connections()

    logger.info("google calendar sync complete: synced_event_count=%s", synced_event_count)
    return synced_event_count


async def _run_google_calendar_sync_worker(poll_seconds: int) -> None:
    while True:
        started_at = datetime.now(UTC)
        synced_event_count: int | None = None
        try:
            synced_event_count = await _run_google_calendar_sync()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("google calendar sync worker iteration failed")
        finally:
            duration_ms = int((datetime.now(UTC) - started_at).total_seconds() * 1000)
            log_with_fields(
                logger,
                logging.INFO,
                "google calendar sync worker iteration complete",
                duration_ms=duration_ms,
                synced_event_count=synced_event_count,
            )
        await asyncio.sleep(poll_seconds)


async def _check_db(*, timeout_seconds: float) -> None:
    async def _ping() -> None:
        async with db.engine.connect() as conn:
            await conn.execute(text("SELECT 1"))

    await asyncio.wait_for(_ping(), timeout=timeout_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(prog="agendable")
    sub = parser.add_subparsers(dest="cmd", required=True)
    settings = get_settings()
    configure_logging(settings)

    sub.add_parser("init-db")
    check_db = sub.add_parser("check-db")
    check_db.add_argument(
        "--timeout-seconds",
        type=float,
        default=5.0,
        help="Fail if the DB ping exceeds this timeout.",
    )
    sub.add_parser("run-reminders")
    sub.add_parser("run-google-calendar-sync")
    worker = sub.add_parser("run-reminders-worker")
    worker.add_argument(
        "--poll-seconds",
        type=int,
        default=settings.reminder_worker_poll_seconds,
    )
    google_worker = sub.add_parser("run-google-calendar-sync-worker")
    google_worker.add_argument(
        "--poll-seconds",
        type=int,
        default=settings.google_calendar_sync_worker_poll_seconds,
    )

    args = parser.parse_args()

    if args.cmd == "init-db":
        asyncio.run(_init_db())
    elif args.cmd == "check-db":
        timeout_seconds = max(0.1, float(args.timeout_seconds))
        try:
            asyncio.run(_check_db(timeout_seconds=timeout_seconds))
        except Exception:
            logger.exception("db healthcheck failed")
            raise SystemExit(1) from None
    elif args.cmd == "run-reminders":
        asyncio.run(_run_due_reminders())
    elif args.cmd == "run-reminders-worker":
        poll_seconds = max(1, int(args.poll_seconds))
        asyncio.run(_run_reminders_worker(poll_seconds))
    elif args.cmd == "run-google-calendar-sync":
        asyncio.run(_run_google_calendar_sync())
    elif args.cmd == "run-google-calendar-sync-worker":
        poll_seconds = max(1, int(args.poll_seconds))
        asyncio.run(_run_google_calendar_sync_worker(poll_seconds))
    else:
        raise SystemExit(2)

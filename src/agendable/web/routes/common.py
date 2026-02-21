from __future__ import annotations

from datetime import UTC, date, datetime, time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from fastapi.templating import Jinja2Templates

from agendable.sso_google import build_oauth


def parse_dt(value: str) -> datetime:
    # Expect HTML datetime-local (no timezone). Treat as UTC for now.
    try:
        dt = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid datetime") from exc

    if dt.tzinfo is None:
        return dt.replace(tzinfo=UTC)
    return dt


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid date") from exc


def parse_time(value: str) -> time:
    try:
        return time.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="Invalid time") from exc


def parse_timezone(value: str) -> ZoneInfo:
    name = value.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Invalid timezone")
    try:
        return ZoneInfo(name)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=400, detail="Unknown timezone") from exc


templates_dir = Path(__file__).resolve().parent.parent / "templates"
templates = Jinja2Templates(directory=str(templates_dir))

oauth = build_oauth()

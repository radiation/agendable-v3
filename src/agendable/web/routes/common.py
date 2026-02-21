from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from fastapi.templating import Jinja2Templates

from agendable.sso_google import build_oauth

_COMMON_TIMEZONES: tuple[tuple[str, str], ...] = (
    ("UTC", "UTC"),
    ("US East", "America/New_York"),
    ("US Central", "America/Chicago"),
    ("US Mountain", "America/Denver"),
    ("US Pacific", "America/Los_Angeles"),
    ("US Alaska", "America/Anchorage"),
    ("US Hawaii", "Pacific/Honolulu"),
    ("Canada Atlantic", "America/Halifax"),
    ("Brazil East", "America/Sao_Paulo"),
    ("UK", "Europe/London"),
    ("Central Europe", "Europe/Berlin"),
    ("Eastern Europe", "Europe/Athens"),
    ("Turkey", "Europe/Istanbul"),
    ("South Africa", "Africa/Johannesburg"),
    ("India", "Asia/Kolkata"),
    ("Pakistan", "Asia/Karachi"),
    ("Bangladesh", "Asia/Dhaka"),
    ("Southeast Asia", "Asia/Bangkok"),
    ("Singapore", "Asia/Singapore"),
    ("China", "Asia/Shanghai"),
    ("Japan", "Asia/Tokyo"),
    ("Korea", "Asia/Seoul"),
    ("Australia East", "Australia/Sydney"),
    ("Australia Central", "Australia/Adelaide"),
    ("New Zealand", "Pacific/Auckland"),
)


def _format_gmt_offset(offset: timedelta | None) -> str:
    if offset is None:
        return "+00:00"
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    absolute_minutes = abs(total_minutes)
    hours, minutes = divmod(absolute_minutes, 60)
    return f"{sign}{hours:02d}:{minutes:02d}"


def _build_timezone_options() -> tuple[tuple[str, str], ...]:
    now_utc = datetime.now(UTC)
    options: list[tuple[str, str]] = []
    for label, zone_name in _COMMON_TIMEZONES:
        zone = ZoneInfo(zone_name)
        offset = now_utc.astimezone(zone).utcoffset()
        options.append((zone_name, f"{label} (GMT {_format_gmt_offset(offset)})"))
    return tuple(options)


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
templates.env.globals["timezone_options"] = _build_timezone_options()

oauth = build_oauth()

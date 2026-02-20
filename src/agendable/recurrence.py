from __future__ import annotations

import itertools
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime

from dateutil.rrule import rrulestr

_WEEKDAYS = ("MO", "TU", "WE", "TH", "FR", "SA", "SU")

_WEEKDAY_LABELS: dict[str, str] = {
    "MO": "Mon",
    "TU": "Tue",
    "WE": "Wed",
    "TH": "Thu",
    "FR": "Fri",
    "SA": "Sat",
    "SU": "Sun",
}

_SETPOS_LABELS: dict[int, str] = {
    1: "1st",
    2: "2nd",
    3: "3rd",
    4: "4th",
    5: "5th",
    -1: "last",
}


def _weekday_for_dtstart(dtstart: datetime) -> str:
    # Python: Monday=0 .. Sunday=6
    idx = dtstart.weekday()
    return _WEEKDAYS[idx]


def _unique_preserve_order[T](values: Iterable[T]) -> list[T]:
    out: list[T] = []
    seen: set[T] = set()
    for v in values:
        if v in seen:
            continue
        seen.add(v)
        out.append(v)
    return out


def normalize_rrule(rrule: str) -> str:
    value = rrule.strip()
    if value.upper().startswith("RRULE:"):
        value = value[6:]
    return value.strip()


def _parse_rrule_parts(rrule: str) -> dict[str, list[str]]:
    parts: dict[str, list[str]] = {}
    normalized = normalize_rrule(rrule)
    if not normalized:
        return parts

    for chunk in normalized.split(";"):
        if not chunk or "=" not in chunk:
            continue
        key, value = chunk.split("=", 1)
        key = key.strip().upper()
        values = [v.strip() for v in value.split(",") if v.strip()]
        if not values:
            continue
        parts[key] = values
    return parts


def describe_recurrence(
    *,
    rrule: str,
    dtstart: datetime | None = None,
    timezone: str | None = None,
) -> str:
    """Return a short, human-readable description of an RRULE.

    This intentionally focuses on the subset of RRULEs our UI can generate.
    For unknown/unexpected rules, it returns "Custom recurrence".
    """

    parts = _parse_rrule_parts(rrule)
    freq = (parts.get("FREQ", [""])[0] or "").upper()

    interval = 1
    try:
        interval = int(parts.get("INTERVAL", ["1"])[0])
    except ValueError:
        interval = 1
    if interval < 1:
        interval = 1

    if dtstart is not None and dtstart.tzinfo is None:
        dtstart = dtstart.replace(tzinfo=UTC)

    time_suffix = ""
    if dtstart is not None:
        time_suffix = f" at {dtstart:%H:%M}"
        if timezone:
            time_suffix = f"{time_suffix} {timezone}"

    if freq == "DAILY":
        if interval == 1:
            return f"Daily{time_suffix}"
        return f"Every {interval} days{time_suffix}"

    if freq == "WEEKLY":
        byday = parts.get("BYDAY")
        if not byday and dtstart is not None:
            byday = [_weekday_for_dtstart(dtstart)]
        if not byday:
            return "Weekly"

        day_labels = [
            _WEEKDAY_LABELS.get(d.strip().upper(), d.strip().upper()) for d in byday if d.strip()
        ]
        day_labels = _unique_preserve_order(day_labels)
        days_text = ", ".join(day_labels)

        if interval == 1:
            return f"Weekly on {days_text}{time_suffix}"
        return f"Every {interval} weeks on {days_text}{time_suffix}"

    if freq == "MONTHLY":
        bymonthday_values = parts.get("BYMONTHDAY")
        byday_values = parts.get("BYDAY")
        bysetpos_values = parts.get("BYSETPOS")

        if bymonthday_values is None and dtstart is not None:
            bymonthday_values = [str(dtstart.day)]

        if byday_values is None and dtstart is not None:
            byday_values = [_weekday_for_dtstart(dtstart)]

        # Month-day style: BYMONTHDAY=15
        if bymonthday_values is not None and "BYSETPOS" not in parts:
            day_num = bymonthday_values[0]
            if interval == 1:
                return f"Monthly on day {day_num}{time_suffix}"
            return f"Every {interval} months on day {day_num}{time_suffix}"

        # Nth weekday style: BYDAY=TH;BYSETPOS=1,3
        if byday_values and bysetpos_values:
            weekday = _WEEKDAY_LABELS.get(byday_values[0].upper(), byday_values[0].upper())
            setpos_ints: list[int] = []
            for raw in bysetpos_values:
                try:
                    setpos_ints.append(int(raw))
                except ValueError:
                    continue

            if not setpos_ints:
                return "Monthly"

            # Display in a stable, natural order (1,2,3,4,5,last)
            setpos_ints = _unique_preserve_order(setpos_ints)
            setpos_ints.sort(key=lambda p: (p == -1, p))

            setpos_labels = [_SETPOS_LABELS.get(p, str(p)) for p in setpos_ints]
            pos_text = (
                " and ".join(setpos_labels) if len(setpos_labels) <= 2 else ", ".join(setpos_labels)
            )

            if interval == 1:
                return f"Monthly on the {pos_text} {weekday}{time_suffix}"
            return f"Every {interval} months on the {pos_text} {weekday}{time_suffix}"

        return "Monthly"

    return "Custom recurrence"


def build_rrule(
    *,
    freq: str,
    interval: int,
    dtstart: datetime,
    weekly_byday: Sequence[str] | None = None,
    monthly_mode: str = "monthday",
    monthly_bymonthday: int | None = None,
    monthly_byday: str | None = None,
    monthly_bysetpos: Sequence[int] | None = None,
) -> str:
    """Build a minimal RRULE string from structured inputs.

    Supported:
    - DAILY: FREQ=DAILY;INTERVAL=n
    - WEEKLY: +BYDAY=MO,TU,...
    - MONTHLY:
        - monthday mode: +BYMONTHDAY=1..31
        - nth_weekday mode: +BYDAY=MO..SU;BYSETPOS=1,2,3,4,-1
    """

    freq_norm = freq.strip().upper()
    if freq_norm not in {"DAILY", "WEEKLY", "MONTHLY"}:
        raise ValueError("Unsupported frequency")

    if interval < 1 or interval > 365:
        raise ValueError("Interval must be between 1 and 365")

    if dtstart.tzinfo is None:
        dtstart = dtstart.replace(tzinfo=UTC)

    parts: list[str] = [f"FREQ={freq_norm}", f"INTERVAL={interval}"]

    if freq_norm == "WEEKLY":
        raw_days = weekly_byday or []
        days = [d.strip().upper() for d in raw_days if d.strip()]
        if not days:
            days = [_weekday_for_dtstart(dtstart)]
        invalid = [d for d in days if d not in _WEEKDAYS]
        if invalid:
            raise ValueError("Invalid weekday")
        parts.append(f"BYDAY={','.join(_unique_preserve_order(days))}")

    if freq_norm == "MONTHLY":
        mode = monthly_mode.strip().lower()
        if mode not in {"monthday", "nth_weekday"}:
            raise ValueError("Invalid monthly mode")

        if mode == "monthday":
            bymonthday = monthly_bymonthday if monthly_bymonthday is not None else dtstart.day
            if bymonthday < 1 or bymonthday > 31:
                raise ValueError("Invalid day of month")
            parts.append(f"BYMONTHDAY={bymonthday}")
        else:
            byday = (monthly_byday or "").strip().upper() or _weekday_for_dtstart(dtstart)
            if byday not in _WEEKDAYS:
                raise ValueError("Invalid weekday")

            raw_setpos = list(monthly_bysetpos or [])
            if not raw_setpos:
                raw_setpos = [1]
            setpos = _unique_preserve_order(raw_setpos)

            allowed = {-1, 1, 2, 3, 4, 5}
            if any(p not in allowed for p in setpos):
                raise ValueError("Invalid set position")

            parts.append(f"BYDAY={byday}")
            parts.append(f"BYSETPOS={','.join(str(p) for p in setpos)}")

    return ";".join(parts)


def generate_datetimes(*, rrule: str, dtstart: datetime, count: int) -> list[datetime]:
    if count <= 0:
        return []

    if dtstart.tzinfo is None:
        dtstart = dtstart.replace(tzinfo=UTC)

    normalized = normalize_rrule(rrule)
    rule = rrulestr(normalized, dtstart=dtstart)

    out: list[datetime] = []
    for dt in itertools.islice(rule, count):
        if dt.tzinfo is None:
            out.append(dt.replace(tzinfo=UTC))
        else:
            out.append(dt)
    return out

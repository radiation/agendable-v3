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


def _coerce_interval(parts: dict[str, list[str]]) -> int:
    try:
        interval = int(parts.get("INTERVAL", ["1"])[0])
    except ValueError:
        interval = 1
    if interval < 1:
        return 1
    return interval


def _time_suffix(dtstart: datetime | None, timezone: str | None) -> str:
    if dtstart is None:
        return ""

    suffix = f" at {dtstart:%H:%M}"
    if timezone:
        return f"{suffix} {timezone}"
    return suffix


def _weekly_day_labels(byday: Sequence[str] | None, dtstart: datetime | None) -> list[str]:
    resolved_days = list(byday) if byday else []
    if not resolved_days and dtstart is not None:
        resolved_days = [_weekday_for_dtstart(dtstart)]
    if not resolved_days:
        return []

    day_labels = [
        _WEEKDAY_LABELS.get(d.strip().upper(), d.strip().upper())
        for d in resolved_days
        if d.strip()
    ]
    return _unique_preserve_order(day_labels)


def _describe_weekly(
    *,
    byday: Sequence[str] | None,
    dtstart: datetime | None,
    interval: int,
    time_suffix: str,
) -> str:
    day_labels = _weekly_day_labels(byday, dtstart)
    if not day_labels:
        return "Weekly"

    days_text = ", ".join(day_labels)
    if interval == 1:
        return f"Weekly on {days_text}{time_suffix}"
    return f"Every {interval} weeks on {days_text}{time_suffix}"


def _format_setpos_text(setpos_values: Sequence[str]) -> str | None:
    setpos_ints: list[int] = []
    for raw in setpos_values:
        try:
            setpos_ints.append(int(raw))
        except ValueError:
            continue

    if not setpos_ints:
        return None

    setpos_ints = _unique_preserve_order(setpos_ints)
    setpos_ints.sort(key=lambda p: (p == -1, p))

    setpos_labels = [_SETPOS_LABELS.get(p, str(p)) for p in setpos_ints]
    if len(setpos_labels) <= 2:
        return " and ".join(setpos_labels)
    return ", ".join(setpos_labels)


def _monthly_values_with_defaults(
    *,
    parts: dict[str, list[str]],
    dtstart: datetime | None,
) -> tuple[list[str] | None, list[str] | None, list[str] | None]:
    bymonthday_values = parts.get("BYMONTHDAY")
    byday_values = parts.get("BYDAY")
    bysetpos_values = parts.get("BYSETPOS")

    if bymonthday_values is None and dtstart is not None:
        bymonthday_values = [str(dtstart.day)]
    if byday_values is None and dtstart is not None:
        byday_values = [_weekday_for_dtstart(dtstart)]

    return bymonthday_values, byday_values, bysetpos_values


def _describe_monthly(
    *,
    parts: dict[str, list[str]],
    dtstart: datetime | None,
    interval: int,
    time_suffix: str,
) -> str:
    bymonthday_values, byday_values, bysetpos_values = _monthly_values_with_defaults(
        parts=parts,
        dtstart=dtstart,
    )

    if bymonthday_values is not None and "BYSETPOS" not in parts:
        day_num = bymonthday_values[0]
        if interval == 1:
            return f"Monthly on day {day_num}{time_suffix}"
        return f"Every {interval} months on day {day_num}{time_suffix}"

    if byday_values and bysetpos_values:
        weekday = _WEEKDAY_LABELS.get(byday_values[0].upper(), byday_values[0].upper())
        pos_text = _format_setpos_text(bysetpos_values)
        if pos_text is None:
            return "Monthly"
        if interval == 1:
            return f"Monthly on the {pos_text} {weekday}{time_suffix}"
        return f"Every {interval} months on the {pos_text} {weekday}{time_suffix}"

    return "Monthly"


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

    interval = _coerce_interval(parts)

    if dtstart is not None and dtstart.tzinfo is None:
        dtstart = dtstart.replace(tzinfo=UTC)

    time_suffix = _time_suffix(dtstart, timezone)

    if freq == "DAILY":
        if interval == 1:
            return f"Daily{time_suffix}"
        return f"Every {interval} days{time_suffix}"

    if freq == "WEEKLY":
        return _describe_weekly(
            byday=parts.get("BYDAY"),
            dtstart=dtstart,
            interval=interval,
            time_suffix=time_suffix,
        )

    if freq == "MONTHLY":
        return _describe_monthly(
            parts=parts,
            dtstart=dtstart,
            interval=interval,
            time_suffix=time_suffix,
        )

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
        parts.append(_build_weekly_byday_part(dtstart=dtstart, weekly_byday=weekly_byday))

    if freq_norm == "MONTHLY":
        parts.extend(
            _build_monthly_parts(
                dtstart=dtstart,
                monthly_mode=monthly_mode,
                monthly_bymonthday=monthly_bymonthday,
                monthly_byday=monthly_byday,
                monthly_bysetpos=monthly_bysetpos,
            )
        )

    return ";".join(parts)


def _build_weekly_byday_part(*, dtstart: datetime, weekly_byday: Sequence[str] | None) -> str:
    raw_days = weekly_byday or []
    days = [d.strip().upper() for d in raw_days if d.strip()]
    if not days:
        days = [_weekday_for_dtstart(dtstart)]

    invalid = [d for d in days if d not in _WEEKDAYS]
    if invalid:
        raise ValueError("Invalid weekday")

    return f"BYDAY={','.join(_unique_preserve_order(days))}"


def _build_monthly_parts(
    *,
    dtstart: datetime,
    monthly_mode: str,
    monthly_bymonthday: int | None,
    monthly_byday: str | None,
    monthly_bysetpos: Sequence[int] | None,
) -> list[str]:
    mode = monthly_mode.strip().lower()
    if mode not in {"monthday", "nth_weekday"}:
        raise ValueError("Invalid monthly mode")

    if mode == "monthday":
        return [
            _build_monthly_bymonthday_part(dtstart=dtstart, monthly_bymonthday=monthly_bymonthday)
        ]

    return _build_monthly_nth_weekday_parts(
        dtstart=dtstart,
        monthly_byday=monthly_byday,
        monthly_bysetpos=monthly_bysetpos,
    )


def _build_monthly_bymonthday_part(*, dtstart: datetime, monthly_bymonthday: int | None) -> str:
    bymonthday = monthly_bymonthday if monthly_bymonthday is not None else dtstart.day
    if bymonthday < 1 or bymonthday > 31:
        raise ValueError("Invalid day of month")
    return f"BYMONTHDAY={bymonthday}"


def _build_monthly_nth_weekday_parts(
    *,
    dtstart: datetime,
    monthly_byday: str | None,
    monthly_bysetpos: Sequence[int] | None,
) -> list[str]:
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

    return [f"BYDAY={byday}", f"BYSETPOS={','.join(str(p) for p in setpos)}"]


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

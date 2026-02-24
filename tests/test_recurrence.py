from __future__ import annotations

from datetime import UTC, datetime

import pytest

from agendable.recurrence import build_rrule, describe_recurrence, generate_datetimes


def test_describe_recurrence_daily_defaults_and_interval_coercion() -> None:
    assert describe_recurrence(rrule="FREQ=DAILY") == "Daily"

    negative_interval = describe_recurrence(
        rrule="FREQ=DAILY;INTERVAL=0",
        dtstart=datetime(2030, 1, 1, 9, 30, tzinfo=UTC),
        timezone="UTC",
    )
    assert negative_interval == "Daily at 09:30 UTC"

    invalid_interval = describe_recurrence(rrule="FREQ=DAILY;INTERVAL=bad")
    assert invalid_interval == "Daily"


def test_describe_recurrence_weekly_with_deduped_byday_and_time_suffix() -> None:
    label = describe_recurrence(
        rrule="FREQ=WEEKLY;INTERVAL=2;BYDAY=MO,MO,TU",
        dtstart=datetime(2030, 1, 1, 8, 0, tzinfo=UTC),
        timezone="UTC",
    )
    assert label == "Every 2 weeks on Mon, Tue at 08:00 UTC"


def test_describe_recurrence_weekly_defaults_to_dtstart_weekday() -> None:
    label = describe_recurrence(
        rrule="FREQ=WEEKLY",
        dtstart=datetime(2030, 1, 1, 9, 0, tzinfo=UTC),
        timezone="UTC",
    )
    assert label == "Weekly on Tue at 09:00 UTC"


def test_describe_recurrence_monthly_day_mode_and_nth_weekday_mode() -> None:
    monthday_label = describe_recurrence(
        rrule="FREQ=MONTHLY;INTERVAL=3;BYMONTHDAY=15",
        dtstart=datetime(2030, 1, 1, 9, 0, tzinfo=UTC),
        timezone="UTC",
    )
    assert monthday_label == "Every 3 months on day 15 at 09:00 UTC"

    nth_weekday_label = describe_recurrence(
        rrule="FREQ=MONTHLY;BYDAY=MO;BYSETPOS=1,3,-1",
        dtstart=datetime(2030, 1, 1, 9, 0, tzinfo=UTC),
        timezone="UTC",
    )
    assert nth_weekday_label == "Monthly on the 1st, 3rd, last Mon at 09:00 UTC"


def test_describe_recurrence_monthly_returns_generic_when_setpos_invalid() -> None:
    label = describe_recurrence(
        rrule="FREQ=MONTHLY;BYDAY=MO;BYSETPOS=bogus",
        dtstart=datetime(2030, 1, 1, 9, 0, tzinfo=UTC),
        timezone="UTC",
    )
    assert label == "Monthly"


def test_describe_recurrence_returns_custom_for_unknown_frequency() -> None:
    assert describe_recurrence(rrule="FREQ=YEARLY;INTERVAL=1") == "Custom recurrence"


def test_build_rrule_weekly_dedupes_days_and_validates_weekday() -> None:
    dtstart = datetime(2030, 1, 1, 9, 0, tzinfo=UTC)

    weekly = build_rrule(
        freq="WEEKLY", interval=1, dtstart=dtstart, weekly_byday=["MO", "MO", " TU "]
    )
    assert weekly == "FREQ=WEEKLY;INTERVAL=1;BYDAY=MO,TU"

    with pytest.raises(ValueError, match="Invalid weekday"):
        build_rrule(freq="WEEKLY", interval=1, dtstart=dtstart, weekly_byday=["XX"])


def test_build_rrule_monthly_monthday_defaults_and_validates_bounds() -> None:
    dtstart = datetime(2030, 1, 22, 9, 0, tzinfo=UTC)

    default_monthday = build_rrule(freq="MONTHLY", interval=1, dtstart=dtstart)
    assert default_monthday == "FREQ=MONTHLY;INTERVAL=1;BYMONTHDAY=22"

    explicit_monthday = build_rrule(
        freq="MONTHLY",
        interval=2,
        dtstart=dtstart,
        monthly_mode="monthday",
        monthly_bymonthday=5,
    )
    assert explicit_monthday == "FREQ=MONTHLY;INTERVAL=2;BYMONTHDAY=5"

    with pytest.raises(ValueError, match="Invalid day of month"):
        build_rrule(
            freq="MONTHLY",
            interval=1,
            dtstart=dtstart,
            monthly_mode="monthday",
            monthly_bymonthday=0,
        )


def test_build_rrule_monthly_nth_weekday_defaults_and_validates_inputs() -> None:
    dtstart = datetime(2030, 1, 1, 9, 0, tzinfo=UTC)

    default_nth_weekday = build_rrule(
        freq="MONTHLY",
        interval=1,
        dtstart=dtstart,
        monthly_mode="nth_weekday",
    )
    assert default_nth_weekday == "FREQ=MONTHLY;INTERVAL=1;BYDAY=TU;BYSETPOS=1"

    explicit_nth_weekday = build_rrule(
        freq="MONTHLY",
        interval=1,
        dtstart=dtstart,
        monthly_mode="nth_weekday",
        monthly_byday="fr",
        monthly_bysetpos=[3, 3, -1],
    )
    assert explicit_nth_weekday == "FREQ=MONTHLY;INTERVAL=1;BYDAY=FR;BYSETPOS=3,-1"

    with pytest.raises(ValueError, match="Invalid weekday"):
        build_rrule(
            freq="MONTHLY",
            interval=1,
            dtstart=dtstart,
            monthly_mode="nth_weekday",
            monthly_byday="XX",
        )

    with pytest.raises(ValueError, match="Invalid set position"):
        build_rrule(
            freq="MONTHLY",
            interval=1,
            dtstart=dtstart,
            monthly_mode="nth_weekday",
            monthly_bysetpos=[6],
        )


def test_build_rrule_validates_frequency_interval_and_monthly_mode() -> None:
    dtstart = datetime(2030, 1, 1, 9, 0, tzinfo=UTC)

    with pytest.raises(ValueError, match="Unsupported frequency"):
        build_rrule(freq="YEARLY", interval=1, dtstart=dtstart)

    with pytest.raises(ValueError, match="Interval must be between 1 and 365"):
        build_rrule(freq="DAILY", interval=0, dtstart=dtstart)

    with pytest.raises(ValueError, match="Invalid monthly mode"):
        build_rrule(freq="MONTHLY", interval=1, dtstart=dtstart, monthly_mode="bad-mode")


def test_generate_datetimes_handles_zero_count_and_naive_dtstart() -> None:
    dtstart = datetime(2030, 1, 1, 9, 0)

    assert generate_datetimes(rrule="FREQ=DAILY;INTERVAL=1", dtstart=dtstart, count=0) == []

    generated = generate_datetimes(rrule="FREQ=DAILY;INTERVAL=1", dtstart=dtstart, count=2)
    assert len(generated) == 2
    assert generated[0] == datetime(2030, 1, 1, 9, 0, tzinfo=UTC)
    assert generated[1] == datetime(2030, 1, 2, 9, 0, tzinfo=UTC)

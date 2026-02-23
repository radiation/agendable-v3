from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi import HTTPException

from agendable.web.routes.common import (
    format_datetime_for_timezone,
    format_datetime_local_value,
    parse_dt,
    parse_dt_for_timezone,
    parse_timezone,
)


def test_parse_dt_naive_defaults_to_utc() -> None:
    parsed = parse_dt("2030-01-01T09:00")
    assert parsed.tzinfo == UTC
    assert parsed.hour == 9


def test_parse_dt_invalid_raises_http_400() -> None:
    with pytest.raises(HTTPException) as exc_info:
        parse_dt("not-a-datetime")
    assert exc_info.value.status_code == 400


def test_parse_dt_for_timezone_converts_local_to_utc() -> None:
    parsed = parse_dt_for_timezone("2030-01-01T16:00", "America/New_York")
    assert parsed.tzinfo == UTC
    assert parsed == datetime(2030, 1, 1, 21, 0, tzinfo=UTC)


def test_parse_dt_for_timezone_falls_back_to_utc_for_unknown_timezone() -> None:
    parsed = parse_dt_for_timezone("2030-01-01T16:00", "Bad/Zone")
    assert parsed == datetime(2030, 1, 1, 16, 0, tzinfo=UTC)


def test_parse_dt_for_timezone_invalid_raises_http_400() -> None:
    with pytest.raises(HTTPException) as exc_info:
        parse_dt_for_timezone("bad-datetime", "America/New_York")
    assert exc_info.value.status_code == 400


def test_parse_dt_for_timezone_honors_timezone_aware_input() -> None:
    parsed = parse_dt_for_timezone("2030-01-01T16:00:00+02:00", "America/New_York")
    assert parsed == datetime(2030, 1, 1, 14, 0, tzinfo=UTC)


def test_parse_timezone_rejects_blank() -> None:
    with pytest.raises(HTTPException) as exc_info:
        parse_timezone("   ")
    assert exc_info.value.status_code == 400


def test_parse_timezone_rejects_unknown() -> None:
    with pytest.raises(HTTPException) as exc_info:
        parse_timezone("Mars/Phobos")
    assert exc_info.value.status_code == 400


def test_format_datetime_for_timezone_formats_with_zone() -> None:
    dt = datetime(2030, 1, 1, 21, 0, tzinfo=UTC)
    formatted = format_datetime_for_timezone(dt, "America/New_York")
    assert formatted == "2030-01-01 04:00 PM EST"


def test_format_datetime_for_timezone_returns_empty_for_non_datetime() -> None:
    assert format_datetime_for_timezone("2030-01-01", "UTC") == ""


def test_format_datetime_for_timezone_falls_back_to_utc_for_unknown_timezone() -> None:
    dt = datetime(2030, 1, 1, 21, 0, tzinfo=UTC)
    formatted = format_datetime_for_timezone(dt, "Unknown/Zone")
    assert formatted == "2030-01-01 09:00 PM UTC"


def test_format_datetime_local_value_uses_user_timezone() -> None:
    dt = datetime(2030, 1, 1, 21, 0, tzinfo=UTC)
    value = format_datetime_local_value(dt, "America/New_York")
    assert value == "2030-01-01T16:00"


def test_format_datetime_local_value_falls_back_to_utc_for_unknown_timezone() -> None:
    dt = datetime(2030, 1, 1, 21, 0, tzinfo=UTC)
    value = format_datetime_local_value(dt, "Unknown/Zone")
    assert value == "2030-01-01T21:00"

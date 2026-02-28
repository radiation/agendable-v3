from __future__ import annotations

import pytest
from httpx import AsyncClient

from agendable.testing.web_test_helpers import login_user
from agendable.web.routes import series as series_routes


@pytest.mark.asyncio
async def test_create_series_maps_service_value_error_to_bad_request(
    client: AsyncClient,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    async def _raise_value_error(*args: object, **kwargs: object) -> tuple[object, list[object]]:
        raise ValueError("service rejected series")

    monkeypatch.setattr(series_routes, "create_series_with_occurrences", _raise_value_error)

    resp = await client.post(
        "/series",
        data={
            "title": "Service Error",
            "reminder_minutes_before": 60,
            "recurrence_start_date": "2030-01-01",
            "recurrence_time": "09:00",
            "recurrence_timezone": "UTC",
            "recurrence_freq": "DAILY",
            "recurrence_interval": 1,
            "generate_count": 1,
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "service rejected series"


async def test_create_series_rejects_invalid_generate_count(client: AsyncClient) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    resp = await client.post(
        "/series",
        data={
            "title": "Invalid Count",
            "reminder_minutes_before": 60,
            "recurrence_start_date": "2030-01-01",
            "recurrence_time": "09:00",
            "recurrence_timezone": "UTC",
            "recurrence_freq": "DAILY",
            "recurrence_interval": 1,
            "generate_count": 0,
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "generate_count must be between 1 and 200"


async def test_create_series_rejects_invalid_monthly_bymonthday(client: AsyncClient) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    resp = await client.post(
        "/series",
        data={
            "title": "Invalid Month Day",
            "reminder_minutes_before": 60,
            "recurrence_start_date": "2030-01-01",
            "recurrence_time": "09:00",
            "recurrence_timezone": "UTC",
            "recurrence_freq": "MONTHLY",
            "recurrence_interval": 1,
            "monthly_mode": "monthday",
            "monthly_bymonthday": "not-a-number",
            "generate_count": 1,
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid monthly day"


async def test_create_series_rejects_invalid_recurrence_interval(client: AsyncClient) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    resp = await client.post(
        "/series",
        data={
            "title": "Invalid Interval",
            "reminder_minutes_before": 60,
            "recurrence_start_date": "2030-01-01",
            "recurrence_time": "09:00",
            "recurrence_timezone": "UTC",
            "recurrence_freq": "DAILY",
            "recurrence_interval": 0,
            "generate_count": 1,
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "recurrence_interval must be between 1 and 365"


async def test_create_series_rejects_invalid_recurrence_settings(client: AsyncClient) -> None:
    await login_user(client, "alice@example.com", "pw-alice")

    resp = await client.post(
        "/series",
        data={
            "title": "Invalid Rule",
            "reminder_minutes_before": 60,
            "recurrence_start_date": "2030-01-01",
            "recurrence_time": "09:00",
            "recurrence_timezone": "UTC",
            "recurrence_freq": "NOT_A_FREQ",
            "recurrence_interval": 1,
            "generate_count": 1,
        },
    )

    assert resp.status_code == 400
    assert resp.json()["detail"] == "Invalid recurrence settings"

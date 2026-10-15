"""Tests for pushbullet_calend.calendar_client."""

from datetime import datetime
from unittest.mock import MagicMock

import pytest

from pushbullet_calend import calendar_client
from pushbullet_calend.calendar_client import (
    CalendarEvent,
    _authenticate,
    fetch_events,
    fetch_events_with_retry,
)
from pushbullet_calend.config import GoogleConfig

_CONFIG = GoogleConfig()


@pytest.fixture
def sleeps(monkeypatch):
    """Record time.sleep calls instead of sleeping."""
    recorded = []
    monkeypatch.setattr(calendar_client.time, "sleep", recorded.append)
    return recorded


def _stub_fetch(monkeypatch, outcomes):
    """Replace fetch_events with a stub yielding successive outcomes.

    Each outcome is either an exception (raised) or a value (returned).
    Returns the list of recorded calls.
    """
    calls = []

    def fake_fetch(config, *, lookahead_days=7):
        calls.append(lookahead_days)
        outcome = outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(calendar_client, "fetch_events", fake_fetch)
    return calls


def test_success_first_attempt_no_sleep(monkeypatch, sleeps):
    calls = _stub_fetch(monkeypatch, [["event"]])

    result = fetch_events_with_retry(_CONFIG)

    assert result == ["event"]
    assert len(calls) == 1
    assert sleeps == []


def test_retries_with_doubling_delay(monkeypatch, sleeps):
    calls = _stub_fetch(monkeypatch, [OSError("down"), OSError("down"), ["event"]])

    result = fetch_events_with_retry(_CONFIG)

    assert result == ["event"]
    assert len(calls) == 3
    assert sleeps == [5, 10]


def test_raises_after_all_attempts_exhausted(monkeypatch, sleeps):
    calls = _stub_fetch(monkeypatch, [OSError(f"down {n}") for n in range(6)])

    with pytest.raises(OSError, match="down 5"):
        fetch_events_with_retry(_CONFIG)

    assert len(calls) == 6
    assert sleeps == [5, 10, 20, 40, 80]


def test_zero_retries_fails_immediately(monkeypatch, sleeps):
    calls = _stub_fetch(monkeypatch, [OSError("down")])

    with pytest.raises(OSError):
        fetch_events_with_retry(_CONFIG, retries=0)

    assert len(calls) == 1
    assert sleeps == []


def test_passes_through_lookahead_days(monkeypatch, sleeps):
    calls = _stub_fetch(monkeypatch, [OSError("down"), ["event"]])

    fetch_events_with_retry(_CONFIG, lookahead_days=14)

    assert calls == [14, 14]


@pytest.mark.parametrize(
    "item, expected",
    [
        (
            {
                "id": "evt_1",
                "summary": "Dentist",
                "description": "SMS: -1h | 555 | Soon",
                "start": {"dateTime": "2026-11-02T09:00:00+10:00"},
            },
            CalendarEvent(
                event_id="evt_1",
                summary="Dentist",
                description="SMS: -1h | 555 | Soon",
                start=datetime.fromisoformat("2026-11-02T09:00:00+10:00"),
            ),
        ),
        (
            {"id": "evt_2", "start": {"dateTime": "2026-11-02T09:00:00+10:00"}},
            CalendarEvent(
                event_id="evt_2",
                summary="",
                description="",
                start=datetime.fromisoformat("2026-11-02T09:00:00+10:00"),
            ),
        ),
    ],
    ids=["all_fields", "missing_optional_fields"],
)
def test_fetch_events_parses_items(monkeypatch, item, expected):
    service = MagicMock()
    service.events().list().execute.return_value = {"items": [item]}
    monkeypatch.setattr(calendar_client, "_authenticate", lambda config: None)
    monkeypatch.setattr(calendar_client, "build", lambda *args, **kwargs: service)

    assert fetch_events(_CONFIG) == [expected]


def test_fetch_events_pages_through_each_calendar(monkeypatch):
    service = MagicMock()
    service.events().list().execute.side_effect = [
        {
            "items": [{"id": "work_1", "start": {"dateTime": "2026-11-02T09:00:00+10:00"}}],
            "nextPageToken": "p2",
        },
        {"items": [{"id": "work_2", "start": {"dateTime": "2026-11-02T09:00:00+10:00"}}]},
        {"items": [{"id": "home_1", "start": {"dateTime": "2026-11-02T09:00:00+10:00"}}]},
    ]
    monkeypatch.setattr(calendar_client, "_authenticate", lambda config: None)
    monkeypatch.setattr(calendar_client, "build", lambda *args, **kwargs: service)

    events = fetch_events(GoogleConfig(calendar_ids=["work", "home"]))

    assert [e.event_id for e in events] == ["work_1", "work_2", "home_1"]


@pytest.mark.parametrize(
    "token_on_disk, expired, refresh_error, expected_saved_token",
    [
        (True, False, None, "cached"),
        (True, True, None, "cached"),
        (True, True, Exception("revoked"), "fresh"),
        (False, False, None, "fresh"),
    ],
    ids=["valid_token_reused", "expired_token_refreshed", "failed_refresh_reauths", "no_token"],
)
def test_authenticate_saves_token(
    monkeypatch, tmp_path, token_on_disk, expired, refresh_error, expected_saved_token
):
    token_path = tmp_path / "token.json"
    if token_on_disk:
        token_path.write_text("{}")
    cached = MagicMock(valid=True, expired=expired, refresh_token="refresh")
    cached.to_json.return_value = "cached"
    cached.refresh.side_effect = refresh_error
    flow = MagicMock()
    flow.run_local_server.return_value.to_json.return_value = "fresh"
    monkeypatch.setattr(
        calendar_client.Credentials, "from_authorized_user_file", lambda path, scopes: cached
    )
    monkeypatch.setattr(
        calendar_client.InstalledAppFlow, "from_client_secrets_file", lambda path, scopes: flow
    )

    _authenticate(GoogleConfig(token_file=str(token_path)))

    assert token_path.read_text() == expected_saved_token

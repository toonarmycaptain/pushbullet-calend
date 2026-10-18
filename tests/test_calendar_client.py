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
    scan_calendars,
)
from pushbullet_calend.config import GoogleConfig

_CONFIG = GoogleConfig(calendar_ids=["primary"])


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

    def fake_fetch(config, *, lookahead_days=7, last_known=None, failed=None):
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


@pytest.mark.parametrize(
    "response, expected_start",
    [
        (
            {
                "timeZone": "Australia/Brisbane",
                "items": [{"id": "bday", "start": {"date": "2026-11-03"}}],
            },
            "2026-11-03T00:00:00+10:00",
        ),
        (
            {
                "timeZone": "America/Chicago",
                "items": [{"id": "bday", "start": {"date": "2026-11-03"}}],
            },
            "2026-11-03T00:00:00-06:00",
        ),
        (
            {
                "timeZone": "Australia/Brisbane",
                "items": [
                    {"id": "bday", "start": {"date": "2026-11-03", "timeZone": "Europe/London"}}
                ],
            },
            "2026-11-03T00:00:00+00:00",
        ),
        (
            {"items": [{"id": "bday", "start": {"date": "2026-11-03"}}]},
            datetime(2026, 11, 3).astimezone().isoformat(),
        ),
    ],
    ids=["calendar_zone", "calendar_zone_negative_offset", "event_zone_wins", "local_fallback"],
)
def test_fetch_events_all_day_starts_at_midnight(monkeypatch, response, expected_start):
    service = MagicMock()
    service.events().list().execute.return_value = response
    monkeypatch.setattr(calendar_client, "_authenticate", lambda config: None)
    monkeypatch.setattr(calendar_client, "build", lambda *args, **kwargs: service)

    # raises value error if there's more than one event
    [event] = fetch_events(_CONFIG)

    assert event.start.isoformat() == expected_start


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
    "responses, last_known, expected_ids",
    [
        (
            [
                OSError("503"),
                {"items": [{"id": "home_1", "start": {"dateTime": "2026-11-02T09:00:00+10:00"}}]},
                {"items": [{"id": "work_1", "start": {"dateTime": "2026-11-02T09:00:00+10:00"}}]},
            ],
            {},
            ["work_1", "home_1"],
        ),
        (
            [
                OSError("503"),
                {"items": [{"id": "home_1", "start": {"dateTime": "2026-11-02T09:00:00+10:00"}}]},
                OSError("503"),
            ],
            {},
            ["home_1"],
        ),
        (
            [
                OSError("503"),
                {"items": [{"id": "home_1", "start": {"dateTime": "2026-11-02T09:00:00+10:00"}}]},
                OSError("503"),
            ],
            {
                "work": [
                    CalendarEvent(
                        event_id="work_old",
                        summary="",
                        description="",
                        start=datetime.fromisoformat("2026-11-02T09:00:00+10:00"),
                    )
                ]
            },
            ["work_old", "home_1"],
        ),
    ],
    ids=["retry_recovers", "fails_twice_skipped", "fails_twice_uses_last_known"],
)
def test_fetch_events_retries_a_failing_calendar_once(
    monkeypatch, responses, last_known, expected_ids
):
    service = MagicMock()
    service.events().list().execute.side_effect = responses
    monkeypatch.setattr(calendar_client, "_authenticate", lambda config: None)
    monkeypatch.setattr(calendar_client, "build", lambda *args, **kwargs: service)

    events = fetch_events(GoogleConfig(calendar_ids=["work", "home"]), last_known=last_known)

    assert [e.event_id for e in events] == expected_ids


def test_fetch_events_remembers_each_calendars_events(monkeypatch):
    service = MagicMock()
    service.events().list().execute.side_effect = [
        {"items": [{"id": "work_1", "start": {"dateTime": "2026-11-02T09:00:00+10:00"}}]},
        {"items": [{"id": "home_1", "start": {"dateTime": "2026-11-02T09:00:00+10:00"}}]},
    ]
    monkeypatch.setattr(calendar_client, "_authenticate", lambda config: None)
    monkeypatch.setattr(calendar_client, "build", lambda *args, **kwargs: service)
    last_known = {}

    fetch_events(GoogleConfig(calendar_ids=["work", "home"]), last_known=last_known)

    assert {calendar: [e.event_id for e in events] for calendar, events in last_known.items()} == {
        "work": ["work_1"],
        "home": ["home_1"],
    }


@pytest.mark.parametrize(
    "calendar_list, expected_failed",
    [
        (
            {
                "items": [
                    {"id": "me@example.com", "summary": "me@example.com", "primary": True},
                    {"id": "kids", "summary": "Kids"},
                ]
            },
            [("me@example.com", "503 again"), ("Kids", "503 again")],
        ),
        (
            {
                "items": [
                    {"id": "me@example.com", "summary": "me@example.com", "primary": True},
                    {"id": "kids", "summary": "kids", "summaryOverride": "The kids"},
                ]
            },
            [("me@example.com", "503 again"), ("The kids", "503 again")],
        ),
        (OSError("list down"), [("primary", "503 again"), ("kids", "503 again")]),
    ],
    ids=["named", "renamed_in_your_list", "names_unavailable_uses_ids"],
)
def test_fetch_events_reports_calendars_that_fail_their_retry(
    monkeypatch, calendar_list, expected_failed
):
    service = MagicMock()
    service.calendarList().list().execute.side_effect = [calendar_list]
    service.events().list().execute.side_effect = [
        OSError("503"),
        OSError("503"),
        {"items": [{"id": "home_1", "start": {"dateTime": "2026-11-02T09:00:00+10:00"}}]},
        OSError("503 again"),
        OSError("503 again"),
    ]
    monkeypatch.setattr(calendar_client, "_authenticate", lambda config: None)
    monkeypatch.setattr(calendar_client, "build", lambda *args, **kwargs: service)
    failed = []

    fetch_events(GoogleConfig(calendar_ids=["primary", "kids", "home"]), failed=failed)

    assert [(name, str(exc)) for name, exc in failed] == expected_failed


def test_fetch_events_raises_when_every_calendar_fails(monkeypatch):
    service = MagicMock()
    service.events().list().execute.side_effect = [OSError("down")] * 4
    monkeypatch.setattr(calendar_client, "_authenticate", lambda config: None)
    monkeypatch.setattr(calendar_client, "build", lambda *args, **kwargs: service)

    with pytest.raises(OSError):
        fetch_events(GoogleConfig(calendar_ids=["work", "home"]))


def test_fetch_events_keeps_one_copy_of_an_event_on_several_calendars(monkeypatch):
    service = MagicMock()
    service.events().list().execute.side_effect = [
        {
            "items": [
                {"id": "dr_appt", "start": {"dateTime": "2026-10-22T15:45:00-05:00"}},
                {"id": "play", "start": {"dateTime": "2026-10-18T19:00:00-05:00"}},
            ]
        },
        {"items": [{"id": "dr_appt", "start": {"dateTime": "2026-10-22T15:45:00-05:00"}}]},
    ]
    monkeypatch.setattr(calendar_client, "_authenticate", lambda config: None)
    monkeypatch.setattr(calendar_client, "build", lambda *args, **kwargs: service)

    events = fetch_events(GoogleConfig(calendar_ids=["primary", "both"]))

    assert [e.event_id for e in events] == ["dr_appt", "play"]


@pytest.mark.parametrize(
    "calendar_ids, expected_fetched",
    [
        (["primary", "work"], ["primary", "work"]),
        (["owned"], ["me@example.com", "kids"]),
        (["all"], ["me@example.com", "kids", "shared@example.com", "holidays"]),
        (["owned", "shared@example.com"], ["me@example.com", "kids", "shared@example.com"]),
        (["kids", "all"], ["kids", "me@example.com", "shared@example.com", "holidays"]),
    ],
    ids=["explicit_ids", "owned", "all_skips_free_busy", "owned_plus_explicit", "deduped"],
)
def test_fetch_events_expands_calendar_selectors(monkeypatch, calendar_ids, expected_fetched):
    service = MagicMock()
    service.calendarList().list().execute.side_effect = [
        {
            "items": [
                {"id": "me@example.com", "accessRole": "owner", "primary": True},
                {"id": "kids", "accessRole": "owner"},
                {"id": "shared@example.com", "accessRole": "writer"},
            ],
            "nextPageToken": "p2",
        },
        {
            "items": [
                {"id": "holidays", "accessRole": "reader"},
                {"id": "busy@example.com", "accessRole": "freeBusyReader"},
            ]
        },
    ]
    list_events = service.events.return_value.list
    list_events.return_value.execute.return_value = {"items": []}
    monkeypatch.setattr(calendar_client, "_authenticate", lambda config: None)
    monkeypatch.setattr(calendar_client, "build", lambda *args, **kwargs: service)

    fetch_events(GoogleConfig(calendar_ids=calendar_ids))

    assert [c.kwargs["calendarId"] for c in list_events.call_args_list] == expected_fetched


@pytest.mark.parametrize(
    "calendar_ids, expected",
    [
        (
            ["primary"],
            [
                ("me@example.com", True, ["evt"]),
                ("holidays", False, ["evt"]),
                ("busy@example.com", False, []),
            ],
        ),
        (
            ["all"],
            [
                ("me@example.com", True, ["evt"]),
                ("holidays", True, ["evt"]),
                ("busy@example.com", False, []),
            ],
        ),
        (
            ["holidays"],
            [
                ("me@example.com", False, ["evt"]),
                ("holidays", True, ["evt"]),
                ("busy@example.com", False, []),
            ],
        ),
    ],
    ids=["primary_alias", "all", "explicit_id"],
)
def test_scan_calendars(monkeypatch, calendar_ids, expected):
    service = MagicMock()
    service.calendarList().list().execute.return_value = {
        "items": [
            {"id": "me@example.com", "accessRole": "owner", "primary": True},
            {"id": "holidays", "accessRole": "reader"},
            {"id": "busy@example.com", "accessRole": "freeBusyReader"},
        ]
    }
    service.events().list().execute.return_value = {
        "items": [{"id": "evt", "start": {"dateTime": "2026-11-02T09:00:00+10:00"}}]
    }
    monkeypatch.setattr(calendar_client, "_authenticate", lambda config: None)
    monkeypatch.setattr(calendar_client, "build", lambda *args, **kwargs: service)

    scanned = scan_calendars(GoogleConfig(calendar_ids=calendar_ids))

    assert [
        (c["id"], selected, [e.event_id for e in events]) for c, selected, events in scanned
    ] == expected


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

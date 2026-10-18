"""Tests for pushbullet_calend.main."""

import imaplib
import logging
from dataclasses import replace
from datetime import UTC, datetime, timedelta, timezone
from unittest.mock import MagicMock, call

import pytest

from pushbullet_calend import crypto, main
from pushbullet_calend.calendar_client import CalendarEvent
from pushbullet_calend.config import (
    AppConfig,
    DatabaseConfig,
    EmailWatchConfig,
    EmailWatchRule,
    ScheduleConfig,
)
from pushbullet_calend.db import SentStore
from pushbullet_calend.main import _collect_pending, _poll, _send_due
from pushbullet_calend.sender import PermanentError, TransientError


@pytest.fixture
def store(tmp_path):
    s = SentStore(tmp_path / "test.db")
    yield s
    s.close()


@pytest.fixture
def notifications(monkeypatch):
    """Capture notify_failure calls instead of hitting Pushbullet."""
    recorded = []
    monkeypatch.setattr(main, "notify_failure", lambda config, title, body: recorded.append(title))
    return recorded


def test_fetch_failure_returns_none_and_notifies(monkeypatch, store, notifications):
    def failing_fetch(config, **kwargs):
        raise OSError("network down")

    monkeypatch.setattr(main, "fetch_events_with_retry", failing_fetch)

    result = _poll(AppConfig(), store, None)

    assert result is None
    assert notifications == ["Calendar fetch failed"]


def test_success_with_no_directives_returns_empty_list(monkeypatch, store, notifications):
    events = [
        CalendarEvent(
            event_id="evt_1",
            summary="Dentist",
            description="no directives here",
            start=datetime.now(UTC) + timedelta(days=1),
        )
    ]
    monkeypatch.setattr(main, "fetch_events_with_retry", lambda config, **kwargs: events)

    result = _poll(AppConfig(), store, None)

    assert result == []
    assert notifications == []


@pytest.mark.parametrize(
    "failures, expected_notifications",
    [
        ([], []),
        ([("Work", OSError("503"))], ["1 calendar(s) failed to fetch"]),
        (
            [("Work", OSError("503")), ("Home", OSError("404"))],
            ["2 calendar(s) failed to fetch"],
        ),
    ],
    ids=["none_failed", "one_failed", "several_failed_one_alert"],
)
def test_poll_alerts_once_for_failed_calendars(
    monkeypatch, store, notifications, failures, expected_notifications
):
    def fetch_with_failures(config, *, failed, **kwargs):
        failed.extend(failures)
        return []

    monkeypatch.setattr(main, "fetch_events_with_retry", fetch_with_failures)

    _poll(AppConfig(), store, None)

    assert notifications == expected_notifications


@pytest.mark.parametrize(
    "description, expected_send_times",
    [
        ("no directives here", []),
        ("SMS: -30m | +15555550123 | Leave now", [datetime(2026, 11, 2, 8, 30, tzinfo=UTC)]),
        ("SMS: -0m | +15555550123 | Leave now", []),
    ],
    ids=["no_directives", "directive", "zero_offset"],
)
def test_collect_pending(store, description, expected_send_times):
    event = CalendarEvent(
        event_id="evt_1",
        summary="Dentist",
        description=description,
        start=datetime(2026, 11, 2, 9, 0, tzinfo=UTC),
    )

    pending = _collect_pending([event], store)

    assert [p.send_time for p in pending] == expected_send_times


def test_collect_pending_skips_already_sent(store):
    event = CalendarEvent(
        event_id="evt_1",
        summary="Dentist",
        description="SMS: -30m | +15555550123 | Leave now",
        start=datetime(2026, 11, 2, 9, 0, tzinfo=UTC),
    )
    store.record_sent("evt_1", "2026-11-02T09:00:00+00:00", "+15555550123", "Leave now")

    assert _collect_pending([event], store) == []


@pytest.mark.parametrize(
    "second_event, expected_event_ids",
    [
        (
            CalendarEvent(
                event_id="evt_2",
                summary="Dentist (copy)",
                description="SMS: -30m | +15555550123 | Leave now",
                start=datetime(2026, 11, 2, 9, 0, tzinfo=UTC),
            ),
            ["evt_1"],
        ),
        (
            CalendarEvent(
                event_id="evt_2",
                summary="Checkup",
                description="SMS: -1h | +15555550123 | Leave now",
                start=datetime(2026, 11, 2, 9, 30, tzinfo=UTC),
            ),
            ["evt_1"],
        ),
        (
            CalendarEvent(
                event_id="evt_2",
                summary="Dentist (Chicago)",
                description="SMS: -30m | +15555550123 | Leave now",
                start=datetime(2026, 11, 2, 3, 0, tzinfo=timezone(timedelta(hours=-6))),
            ),
            ["evt_1"],
        ),
        (
            CalendarEvent(
                event_id="evt_2",
                summary="Dentist (other number)",
                description="SMS: -30m | +15555550199 | Leave now",
                start=datetime(2026, 11, 2, 9, 0, tzinfo=UTC),
            ),
            ["evt_1", "evt_2"],
        ),
    ],
    ids=["copied_event", "other_event_same_send_time", "same_instant_other_zone", "other_number"],
)
def test_collect_pending_queues_identical_texts_once(store, second_event, expected_event_ids):
    event = CalendarEvent(
        event_id="evt_1",
        summary="Dentist",
        description="SMS: -30m | +15555550123 | Leave now",
        start=datetime(2026, 11, 2, 9, 0, tzinfo=UTC),
    )

    pending = _collect_pending([event, second_event], store)

    assert [p.event.event_id for p in pending] == expected_event_ids


def test_text_sent_from_one_event_is_not_sent_again_from_a_copy(monkeypatch, store):
    event = CalendarEvent(
        event_id="evt_1",
        summary="Dentist",
        description="SMS: -30m | +15555550123 | Leave now",
        start=datetime.now(UTC) + timedelta(minutes=10),
    )
    monkeypatch.setattr(main, "send_sms", MagicMock())
    _send_due(_collect_pending([event], store), AppConfig(), store)

    assert _collect_pending([replace(event, event_id="evt_2")], store) == []


@pytest.mark.parametrize(
    "start, send_error, expected_sent, expected_notifications",
    [
        (datetime.now(UTC) + timedelta(minutes=10), None, 1, []),
        (datetime.now(UTC) + timedelta(hours=2), None, 0, []),
        (datetime.now(UTC) - timedelta(minutes=5), None, 0, []),
        (datetime.now(UTC) + timedelta(minutes=10), TransientError("timeout"), 0, []),
        (
            datetime.now(UTC) + timedelta(minutes=10),
            PermanentError("400 Bad Request"),
            0,
            ["SMS to +15555550123 failed (1x)"],
        ),
    ],
    ids=["due", "not_due_yet", "event_started", "transient_error", "permanent_error"],
)
def test_send_due(
    monkeypatch, store, notifications, start, send_error, expected_sent, expected_notifications
):
    event = CalendarEvent(
        event_id="evt_1",
        summary="Dentist",
        description="SMS: -30m | +15555550123 | Leave now",
        start=start,
    )
    monkeypatch.setattr(main, "send_sms", MagicMock(side_effect=send_error))
    pending = _collect_pending([event], store)

    assert _send_due(pending, AppConfig(), store) == expected_sent
    assert notifications == expected_notifications


@pytest.mark.parametrize(
    "fresh_event_id, fresh_description, expected_sent",
    [
        ("evt_1", "SMS: -30m | +15555550123 | Leave now", 1),
        ("evt_2", "SMS: -30m | +15555550123 | Leave now", 0),
        ("evt_1", "directive removed", 0),
        ("evt_1", "SMS: -30m | +15555550123 | Different text", 0),
    ],
    ids=["unchanged", "event_deleted", "directive_removed", "message_changed"],
)
def test_send_due_verifies_against_fresh_events(
    monkeypatch, store, fresh_event_id, fresh_description, expected_sent
):
    event = CalendarEvent(
        event_id="evt_1",
        summary="Dentist",
        description="SMS: -30m | +15555550123 | Leave now",
        start=datetime.now(UTC) + timedelta(minutes=10),
    )
    fresh_event = replace(event, event_id=fresh_event_id, description=fresh_description)
    monkeypatch.setattr(main, "fetch_events_with_retry", MagicMock(return_value=[fresh_event]))
    monkeypatch.setattr(main, "send_sms", MagicMock())
    pending = _collect_pending([event], store)

    assert _send_due(pending, AppConfig(), store, verify=True) == expected_sent


def test_send_due_skips_cycle_when_verification_fetch_fails(monkeypatch, store):
    event = CalendarEvent(
        event_id="evt_1",
        summary="Dentist",
        description="SMS: -30m | +15555550123 | Leave now",
        start=datetime.now(UTC) + timedelta(minutes=10),
    )
    monkeypatch.setattr(
        main, "fetch_events_with_retry", MagicMock(side_effect=OSError("calendar down"))
    )
    monkeypatch.setattr(main, "send_sms", MagicMock())
    pending = _collect_pending([event], store)

    assert _send_due(pending, AppConfig(), store, verify=True) == 0


@pytest.mark.parametrize(
    "email_result", [2, OSError("imap down")], ids=["email_ok", "email_check_fails"]
)
def test_poll_checks_email_without_losing_calendar_results(
    monkeypatch, store, notifications, email_result
):
    event = CalendarEvent(
        event_id="evt_1",
        summary="Dentist",
        description="SMS: -30m | +15555550123 | Leave now",
        start=datetime(2026, 11, 2, 9, 0, tzinfo=UTC),
    )
    check_email = MagicMock(side_effect=[email_result])
    monkeypatch.setattr(main, "fetch_events_with_retry", MagicMock(return_value=[event]))
    monkeypatch.setattr(main, "check_email", check_email)

    assert len(_poll(AppConfig(), store, "email store")) == 1
    check_email.assert_called_once()


@pytest.mark.parametrize(
    "fetch_result, email_enabled, expected_sms, expected_email_checks",
    [
        (
            [
                CalendarEvent(
                    event_id="evt_1",
                    summary="Dentist",
                    description="SMS: -30m | +15555550123 | Leave now",
                    start=datetime.now(UTC) + timedelta(minutes=10),
                )
            ],
            False,
            1,
            0,
        ),
        (OSError("down"), False, 0, 0),
        (OSError("down"), True, 0, 1),
        (
            [
                CalendarEvent(
                    event_id="evt_1",
                    summary="Dentist",
                    description="SMS: -30m | +15555550123 | Leave now",
                    start=datetime.now(UTC) + timedelta(minutes=10),
                )
            ],
            True,
            1,
            1,
        ),
    ],
    ids=["sends_due", "fetch_fails", "fetch_fails_still_checks_email", "with_email_watch"],
)
def test_run_once(
    monkeypatch,
    tmp_path,
    notifications,
    fetch_result,
    email_enabled,
    expected_sms,
    expected_email_checks,
):
    send_sms = MagicMock()
    check_email = MagicMock(return_value=0)
    monkeypatch.setattr(main, "fetch_events_with_retry", MagicMock(side_effect=[fetch_result]))
    monkeypatch.setattr(main, "send_sms", send_sms)
    monkeypatch.setattr(main, "check_email", check_email)
    config = AppConfig(
        database=DatabaseConfig(path=str(tmp_path / "sent.db")),
        email_watch=EmailWatchConfig(enabled=email_enabled),
    )

    main.run(config)

    assert send_sms.call_count == expected_sms
    assert check_email.call_count == expected_email_checks


@pytest.mark.parametrize(
    "fetch_results, expected_log",
    [
        (
            # The same event for the poll and the pre-send check, then stop.
            [
                [
                    CalendarEvent(
                        event_id="evt_1",
                        summary="Dentist",
                        description="SMS: -30m | +15555550123 | Leave now",
                        start=datetime.now(UTC) + timedelta(minutes=10),
                    )
                ]
            ]
            * 2
            + [KeyboardInterrupt],
            "Sent 1 messages",
        ),
        (
            # A poll, a failed poll, then stop.
            [
                [
                    CalendarEvent(
                        event_id="evt_1",
                        summary="Dentist",
                        description="SMS: -30m | +15555550123 | Leave now",
                        start=datetime.now(UTC) + timedelta(hours=2),
                    )
                ],
                OSError("down"),
                KeyboardInterrupt,
            ],
            "keeping 1 previously pending SMS",
        ),
    ],
    ids=["sends_due_reminder", "keeps_pending_when_poll_fails"],
)
def test_run_daemon(monkeypatch, caplog, store, notifications, fetch_results, expected_log):
    caplog.set_level(logging.INFO)
    # KeyboardInterrupt isn't caught by the daemon loop, so it ends the test run.
    monkeypatch.setattr(main, "fetch_events_with_retry", MagicMock(side_effect=fetch_results))
    monkeypatch.setattr(main, "send_sms", MagicMock())
    monkeypatch.setattr(main.signal, "signal", MagicMock())
    monkeypatch.setattr(main, "SentStore", lambda path: store)

    # A zero poll interval re-polls every loop instead of sleeping.
    with pytest.raises(KeyboardInterrupt):
        main.run(AppConfig(schedule=ScheduleConfig(poll_interval_minutes=0)), daemon=True)

    assert expected_log in caplog.text


@pytest.mark.parametrize(
    "argv, expected_calls",
    [
        ([], [call.run()]),
        (["--daemon"], [call.run(daemon=True)]),
        (["--encrypt-password"], [call.encrypt_password()]),
        (["--test-email"], [call.test_email()]),
        (["--calendars"], [call.show_calendars()]),
        (["--list"], [call.list_pending()]),
    ],
    ids=["once", "daemon", "encrypt_password", "test_email", "calendars", "list"],
)
def test_main_dispatches_on_flags(monkeypatch, argv, expected_calls):
    commands = MagicMock()
    monkeypatch.setattr("sys.argv", ["pushbullet-calend", *argv])
    monkeypatch.setattr(main, "run", commands.run)
    monkeypatch.setattr(main, "_encrypt_password", commands.encrypt_password)
    monkeypatch.setattr(main, "_test_email", commands.test_email)
    monkeypatch.setattr(main, "_show_calendars", commands.show_calendars)
    monkeypatch.setattr(main, "_list_pending", commands.list_pending)

    main.main()

    assert commands.mock_calls == expected_calls


@pytest.mark.parametrize(
    "key_exists, expected_output",
    [
        (False, "Generated new key"),
        (True, "Using existing key"),
    ],
)
def test_encrypt_password(monkeypatch, capsys, tmp_path, key_exists, expected_output):
    key_path = tmp_path / "test.key"
    if key_exists:
        key_path.write_bytes(b"key")
    monkeypatch.setattr(crypto, "_DEFAULT_KEY_PATH", key_path)
    monkeypatch.setattr(crypto, "generate_key", lambda: b"new key")
    monkeypatch.setattr(crypto, "load_key", lambda: b"existing key")
    monkeypatch.setattr(crypto, "encrypt", lambda password, key: "TOKEN")
    monkeypatch.setattr("getpass.getpass", lambda prompt: "hunter2")

    main._encrypt_password()

    output = capsys.readouterr().out
    assert expected_output in output
    assert 'app_password = "TOKEN"' in output


@pytest.mark.parametrize(
    "search_result, fetch_result, expected_output",
    [
        (
            ("OK", [b"1"]),
            ("OK", [(b"1", b"Subject: Alert!\r\nFrom: bot@x.com\r\n\r\n")]),
            "[MATCH] UID 1: Alert! (from: bot@x.com)",
        ),
        (
            ("OK", [b"1"]),
            ("OK", [(b"1", b"Subject: Alert! (resent)\r\nFrom: bot@x.com\r\n\r\n")]),
            "[skip] UID 1: Alert! (resent) (from: bot@x.com)",
        ),
        (("OK", [b""]), None, "No matching emails found."),
        (
            ("OK", [b"1 2 3 4 5 6 7 8 9 10 11 12"]),
            ("OK", [(b"1", b"Subject: Alert!\r\nFrom: bot@x.com\r\n\r\n")]),
            "... and 2 more",
        ),
    ],
    ids=["match", "no_exact_match", "no_emails", "more_than_10"],
)
def test_test_email(monkeypatch, capsys, search_result, fetch_result, expected_output):
    conn = MagicMock()
    conn.search.return_value = search_result
    conn.fetch.return_value = fetch_result
    config = AppConfig(
        email_watch=EmailWatchConfig(
            enabled=True,
            imap_server="imap.example.com",
            email_address="me@example.com",
            rules=[EmailWatchRule(subject="Alert!", phone_number="+15555550123")],
        )
    )
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda server: conn)
    monkeypatch.setattr(main, "load_config", lambda: config)

    main._test_email()

    assert expected_output in capsys.readouterr().out


@pytest.mark.parametrize(
    "config, login_error, expected_output",
    [
        (AppConfig(), None, "email_watch is not enabled"),
        (
            AppConfig(email_watch=EmailWatchConfig(enabled=True, imap_server="imap.example.com")),
            imaplib.IMAP4.error("bad password"),
            "Login FAILED: bad password",
        ),
    ],
    ids=["disabled", "login_fails"],
)
def test_test_email_stops_early(monkeypatch, capsys, config, login_error, expected_output):
    conn = MagicMock()
    conn.login.side_effect = login_error
    monkeypatch.setattr(imaplib, "IMAP4_SSL", lambda server: conn)
    monkeypatch.setattr(main, "load_config", lambda: config)

    main._test_email()

    assert expected_output in capsys.readouterr().out


@pytest.mark.parametrize(
    "expected_output",
    [
        "* owner    Mine  (me@example.com)\n"
        "  owner    Kids  (kids)\n"
        "  reader   Holidays  (holidays)  [hidden]\n",
        "Dentist (1 SMS)",
    ],
    ids=["owned_first_main_on_top", "directive_events"],
)
def test_show_calendars(monkeypatch, capsys, expected_output):
    scanned = [
        (
            {"id": "holidays", "summary": "Holidays", "accessRole": "reader", "hidden": True},
            False,
            [
                CalendarEvent(
                    event_id="evt_1",
                    summary="Dentist",
                    description="SMS: -30m | +15555550123 | Leave now",
                    start=datetime(2026, 11, 2, 9, 0, tzinfo=UTC),
                )
            ],
        ),
        ({"id": "kids", "summary": "Kids", "accessRole": "owner"}, False, []),
        (
            {
                "id": "me@example.com",
                "summary": "me@example.com",
                "summaryOverride": "Mine",
                "accessRole": "owner",
                "primary": True,
            },
            True,
            [],
        ),
    ]
    monkeypatch.setattr(main, "load_config", lambda: AppConfig())
    monkeypatch.setattr(main, "scan_calendars", lambda config, **kwargs: scanned)

    main._show_calendars()

    assert expected_output in capsys.readouterr().out


@pytest.mark.parametrize(
    "events, expected_output",
    [
        ([], "No SMS queued."),
        (
            [
                CalendarEvent(
                    event_id="evt_1",
                    summary="Dentist",
                    description="SMS: -30m | +15555550123 | Leave now",
                    start=datetime(2026, 11, 2, 9, 0, tzinfo=UTC),
                )
            ],
            "+15555550123  'Leave now'  from 'Dentist'\n",
        ),
    ],
    ids=["nothing_queued", "queued"],
)
def test_list_pending(monkeypatch, capsys, tmp_path, events, expected_output):
    config = AppConfig(database=DatabaseConfig(path=str(tmp_path / "sent.db")))
    monkeypatch.setattr(main, "load_config", lambda: config)
    monkeypatch.setattr(main, "fetch_events", lambda config, **kwargs: events)

    main._list_pending()

    assert expected_output in capsys.readouterr().out

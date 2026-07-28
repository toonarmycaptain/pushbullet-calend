"""Tests for pushbullet_calend.main poll failure handling."""

from datetime import UTC, datetime, timedelta

import pytest

from pushbullet_calend import main
from pushbullet_calend.calendar_client import CalendarEvent
from pushbullet_calend.config import AppConfig
from pushbullet_calend.db import SentStore
from pushbullet_calend.main import _poll


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


class TestPoll:
    def test_fetch_failure_returns_none_and_notifies(self, monkeypatch, store, notifications):
        def failing_fetch(config, **kwargs):
            raise OSError("network down")

        monkeypatch.setattr(main, "fetch_events_with_retry", failing_fetch)

        result = _poll(AppConfig(), store, None)

        assert result is None
        assert notifications == ["Calendar fetch failed"]

    def test_success_with_no_directives_returns_empty_list(
        self, monkeypatch, store, notifications
    ):
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

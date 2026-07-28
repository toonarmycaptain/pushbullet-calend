"""Tests for pushbullet_calend.calendar_client retry logic."""

import pytest

from pushbullet_calend import calendar_client
from pushbullet_calend.calendar_client import fetch_events_with_retry
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


class TestFetchEventsWithRetry:
    def test_success_first_attempt_no_sleep(self, monkeypatch, sleeps):
        calls = _stub_fetch(monkeypatch, [["event"]])

        result = fetch_events_with_retry(_CONFIG)

        assert result == ["event"]
        assert len(calls) == 1
        assert sleeps == []

    def test_retries_with_doubling_delay(self, monkeypatch, sleeps):
        calls = _stub_fetch(monkeypatch, [OSError("down"), OSError("down"), ["event"]])

        result = fetch_events_with_retry(_CONFIG)

        assert result == ["event"]
        assert len(calls) == 3
        assert sleeps == [5, 10]

    def test_raises_after_all_attempts_exhausted(self, monkeypatch, sleeps):
        calls = _stub_fetch(monkeypatch, [OSError(f"down {n}") for n in range(6)])

        with pytest.raises(OSError, match="down 5"):
            fetch_events_with_retry(_CONFIG)

        assert len(calls) == 6
        assert sleeps == [5, 10, 20, 40, 80]

    def test_zero_retries_fails_immediately(self, monkeypatch, sleeps):
        calls = _stub_fetch(monkeypatch, [OSError("down")])

        with pytest.raises(OSError):
            fetch_events_with_retry(_CONFIG, retries=0)

        assert len(calls) == 1
        assert sleeps == []

    def test_passes_through_lookahead_days(self, monkeypatch, sleeps):
        calls = _stub_fetch(monkeypatch, [OSError("down"), ["event"]])

        fetch_events_with_retry(_CONFIG, lookahead_days=14)

        assert calls == [14, 14]

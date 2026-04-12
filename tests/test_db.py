"""Tests for pushbullet_calend.db."""

import pytest

from pushbullet_calend.db import SentStore, message_hash


@pytest.fixture
def store(tmp_path):
    s = SentStore(tmp_path / "test.db")
    yield s
    s.close()


_EVENT = ("evt_123", "2026-04-15T09:00:00+10:00", "+61412345678")


class TestMessageHash:
    @pytest.mark.parametrize(
        "text_a, text_b, should_match",
        [
            ("hello", "hello", True),
            ("hello", "Hello", False),
            ("pick up kids", "pick up kids!", False),
        ],
    )
    def test_determinism_and_sensitivity(self, text_a, text_b, should_match):
        assert (message_hash(text_a) == message_hash(text_b)) is should_match


class TestSentStore:
    def test_not_sent_initially(self, store):
        assert not store.was_sent(*_EVENT, "Pick up the kids")

    def test_was_sent_after_recording(self, store):
        store.record_sent(*_EVENT, "Pick up the kids")
        assert store.was_sent(*_EVENT, "Pick up the kids")

    def test_different_message_not_marked_sent(self, store):
        store.record_sent(*_EVENT, "Pick up the kids")
        assert not store.was_sent(*_EVENT, "Different message")

    @pytest.mark.parametrize(
        "varied_event",
        [
            ("other_evt", "2026-04-15T09:00:00+10:00", "+61412345678"),
            ("evt_123", "2026-04-16T09:00:00+10:00", "+61412345678"),
            ("evt_123", "2026-04-15T09:00:00+10:00", "+61499999999"),
        ],
        ids=["different_event_id", "different_instance", "different_phone"],
    )
    def test_different_dedup_key_not_marked_sent(self, store, varied_event):
        store.record_sent(*_EVENT, "Same message")
        assert not store.was_sent(*varied_event, "Same message")

    def test_duplicate_record_is_ignored(self, store):
        store.record_sent(*_EVENT, "msg")
        store.record_sent(*_EVENT, "msg")  # should not raise
        assert store.was_sent(*_EVENT, "msg")

    def test_failed_status_still_counts_as_sent(self, store):
        store.record_sent(*_EVENT, "msg", status="failed")
        assert store.was_sent(*_EVENT, "msg")

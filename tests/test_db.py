"""Tests for pushbullet_calend.db."""

import pytest

from pushbullet_calend.db import SentStore, message_hash


@pytest.fixture
def store(tmp_path):
    s = SentStore(tmp_path / "test.db", max_retries=3)
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


class TestShouldSend:
    def test_new_message_should_send(self, store):
        assert store.should_send(*_EVENT, "Pick up the kids")

    def test_sent_message_should_not_send(self, store):
        store.record_sent(*_EVENT, "Pick up the kids")
        assert not store.should_send(*_EVENT, "Pick up the kids")

    def test_different_message_should_send(self, store):
        store.record_sent(*_EVENT, "Pick up the kids")
        assert store.should_send(*_EVENT, "Different message")

    @pytest.mark.parametrize(
        "varied_event",
        [
            ("other_evt", "2026-04-15T09:00:00+10:00", "+61412345678"),
            ("evt_123", "2026-04-16T09:00:00+10:00", "+61412345678"),
            ("evt_123", "2026-04-15T09:00:00+10:00", "+61499999999"),
        ],
        ids=["different_event_id", "different_instance", "different_phone"],
    )
    def test_different_dedup_key_should_send(self, store, varied_event):
        store.record_sent(*_EVENT, "Same message")
        assert store.should_send(*varied_event, "Same message")

    def test_failed_message_should_retry(self, store):
        store.record_failure(*_EVENT, "msg")
        assert store.should_send(*_EVENT, "msg")

    def test_exhausted_retries_should_not_send(self, store):
        for _ in range(3):
            store.record_failure(*_EVENT, "msg")
        assert not store.should_send(*_EVENT, "msg")


class TestRecordFailure:
    def test_first_failure_returns_1(self, store):
        assert store.record_failure(*_EVENT, "msg") == 1

    def test_increments_retry_count(self, store):
        store.record_failure(*_EVENT, "msg")
        assert store.record_failure(*_EVENT, "msg") == 2

    def test_success_after_failure(self, store):
        store.record_failure(*_EVENT, "msg")
        store.record_sent(*_EVENT, "msg")
        assert not store.should_send(*_EVENT, "msg")


class TestRecordSent:
    def test_duplicate_record_is_idempotent(self, store):
        store.record_sent(*_EVENT, "msg")
        store.record_sent(*_EVENT, "msg")  # should not raise
        assert not store.should_send(*_EVENT, "msg")

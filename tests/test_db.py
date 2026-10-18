"""Tests for pushbullet_calend.db."""

import sqlite3
from datetime import UTC, datetime, timedelta, timezone

import pytest

from pushbullet_calend.db import SentStore, message_hash


@pytest.fixture
def store(tmp_path):
    s = SentStore(tmp_path / "test.db", max_retries=3)
    yield s
    s.close()


_EVENT = ("evt_123", "2026-04-15T09:00:00+10:00", "+61412345678")


@pytest.mark.parametrize(
    "text_a, text_b, should_match",
    [
        ("hello", "hello", True),
        ("hello", "Hello", False),
        ("pick up kids", "pick up kids!", False),
    ],
)
def test_determinism_and_sensitivity(text_a, text_b, should_match):
    assert (message_hash(text_a) == message_hash(text_b)) is should_match


def test_new_message_should_send(store):
    assert store.should_send(*_EVENT, "Pick up the kids")


def test_sent_message_should_not_send(store):
    store.record_sent(*_EVENT, "Pick up the kids")
    assert not store.should_send(*_EVENT, "Pick up the kids")


def test_different_message_should_send(store):
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
def test_different_dedup_key_should_send(store, varied_event):
    store.record_sent(*_EVENT, "Same message")
    assert store.should_send(*varied_event, "Same message")


@pytest.mark.parametrize(
    "varied_event, send_time, expected",
    [
        (
            ("other_evt", "2026-04-15T09:00:00+10:00", "+61412345678"),
            datetime(2026, 4, 15, 8, 30, tzinfo=timezone(timedelta(hours=10))),
            False,
        ),
        (
            ("other_evt", "2026-04-15T09:30:00+10:00", "+61412345678"),
            datetime(2026, 4, 14, 22, 30, tzinfo=UTC),
            False,
        ),
        (
            ("other_evt", "2026-04-15T09:00:00+10:00", "+61412345678"),
            datetime(2026, 4, 15, 8, 45, tzinfo=timezone(timedelta(hours=10))),
            True,
        ),
        (
            ("other_evt", "2026-04-15T09:00:00+10:00", "+61499999999"),
            datetime(2026, 4, 15, 8, 30, tzinfo=timezone(timedelta(hours=10))),
            True,
        ),
    ],
    ids=["same_text_other_event", "same_instant_other_zone", "other_send_time", "other_phone"],
)
def test_same_text_already_sent_from_any_event(store, varied_event, send_time, expected):
    store.record_sent(
        *_EVENT,
        "Same message",
        send_time=datetime(2026, 4, 15, 8, 30, tzinfo=timezone(timedelta(hours=10))),
    )

    assert store.should_send(*varied_event, "Same message", send_time=send_time) is expected


def test_adds_send_time_to_an_older_database(tmp_path):
    path = tmp_path / "old.db"
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE sent_messages (id INTEGER PRIMARY KEY, event_id VARCHAR NOT NULL,"
        " instance_start VARCHAR NOT NULL, phone_number VARCHAR NOT NULL,"
        " message_hash VARCHAR NOT NULL, sent_at VARCHAR NOT NULL, status VARCHAR NOT NULL,"
        " retry_count INTEGER NOT NULL)"
    )
    conn.close()
    send_time = datetime(2026, 4, 15, 8, 30, tzinfo=timezone(timedelta(hours=10)))
    store = SentStore(path)
    store.record_sent(*_EVENT, "msg", send_time=send_time)

    assert not store.should_send("other_evt", *_EVENT[1:], "msg", send_time=send_time)
    store.close()


def test_failed_message_should_retry(store):
    store.record_failure(*_EVENT, "msg")
    assert store.should_send(*_EVENT, "msg")


def test_exhausted_retries_should_not_send(store):
    for _ in range(3):
        store.record_failure(*_EVENT, "msg")
    assert not store.should_send(*_EVENT, "msg")


def test_first_failure_returns_1(store):
    assert store.record_failure(*_EVENT, "msg") == 1


def test_increments_retry_count(store):
    store.record_failure(*_EVENT, "msg")
    assert store.record_failure(*_EVENT, "msg") == 2


def test_success_after_failure(store):
    store.record_failure(*_EVENT, "msg")
    store.record_sent(*_EVENT, "msg")
    assert not store.should_send(*_EVENT, "msg")


def test_duplicate_record_is_idempotent(store):
    store.record_sent(*_EVENT, "msg")
    store.record_sent(*_EVENT, "msg")  # should not raise
    assert not store.should_send(*_EVENT, "msg")

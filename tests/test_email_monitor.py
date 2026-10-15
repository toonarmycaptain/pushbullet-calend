"""Tests for pushbullet_calend.email_monitor."""

import email
import imaplib
from email.mime.text import MIMEText
from unittest.mock import ANY, MagicMock, call, patch

import pytest

from pushbullet_calend.config import (
    AppConfig,
    EmailWatchConfig,
    EmailWatchRule,
    PushbulletConfig,
)
from pushbullet_calend.db import EmailNotificationStore
from pushbullet_calend.email_monitor import (
    _build_search_criteria,
    _check_mailbox,
    _decode_header,
    _matches_rule,
    _rule_key,
    check_email,
)
from pushbullet_calend.sender import PermanentError, TransientError

# ---------------------------------------------------------------------------
# _decode_header
# ---------------------------------------------------------------------------


def _make_msg(**headers: str) -> email.message.Message:
    msg = MIMEText("")
    for k, v in headers.items():
        del msg[k]
        msg[k] = v
    return msg


def test_plain_subject():
    msg = _make_msg(Subject="Hello world")
    assert _decode_header(msg, "Subject") == "Hello world"


def test_missing_header_returns_empty():
    msg = _make_msg()
    assert _decode_header(msg, "X-Missing") == ""


def test_from_header():
    msg = _make_msg(From="Alice <alice@example.com>")
    assert _decode_header(msg, "From") == "Alice <alice@example.com>"


# ---------------------------------------------------------------------------
# _rule_key
# ---------------------------------------------------------------------------


def test_rule_key_subject_only():
    rule = EmailWatchRule(subject="Order shipped", phone_number="+15555550123")
    assert _rule_key(rule) == "Order shipped"


def test_rule_key_sender_only():
    rule = EmailWatchRule(sender="alerts@example.com", phone_number="+15555550123")
    assert _rule_key(rule) == "from:alerts@example.com"


def test_rule_key_both():
    rule = EmailWatchRule(
        subject="Order shipped",
        sender="shop@example.com",
        phone_number="+15555550123",
    )
    assert _rule_key(rule) == "Order shipped|from:shop@example.com"


# ---------------------------------------------------------------------------
# _build_search_criteria
# ---------------------------------------------------------------------------


def test_build_search_criteria_subject_only():
    rule = EmailWatchRule(subject="Hello world", phone_number="+15555550123")
    criteria = _build_search_criteria(rule, "09-Jun-2026")
    assert "SUBJECT" in criteria
    assert "FROM" not in criteria
    assert criteria[-2:] == ["SINCE", "09-Jun-2026"]


def test_build_search_criteria_sender_only():
    rule = EmailWatchRule(sender="bob@example.com", phone_number="+15555550123")
    criteria = _build_search_criteria(rule, "09-Jun-2026")
    assert "FROM" in criteria
    assert '"bob@example.com"' in criteria
    assert "SUBJECT" not in criteria


def test_build_search_criteria_both():
    rule = EmailWatchRule(
        subject="Alert",
        sender="bot@example.com",
        phone_number="+15555550123",
    )
    criteria = _build_search_criteria(rule, "09-Jun-2026")
    assert "SUBJECT" in criteria
    assert "FROM" in criteria


# ---------------------------------------------------------------------------
# _matches_rule
# ---------------------------------------------------------------------------


def test_subject_match():
    msg = _make_msg(Subject="Order shipped", From="shop@example.com")
    rule = EmailWatchRule(subject="Order shipped", phone_number="+15555550123")
    assert _matches_rule(msg, rule)


def test_subject_mismatch():
    msg = _make_msg(Subject="Order delayed", From="shop@example.com")
    rule = EmailWatchRule(subject="Order shipped", phone_number="+15555550123")
    assert not _matches_rule(msg, rule)


def test_sender_match():
    msg = _make_msg(Subject="Whatever", From="alerts@example.com")
    rule = EmailWatchRule(sender="alerts@example.com", phone_number="+15555550123")
    assert _matches_rule(msg, rule)


def test_sender_substring_match():
    msg = _make_msg(Subject="Hi", From="Alert Bot <alerts@example.com>")
    rule = EmailWatchRule(sender="alerts@example.com", phone_number="+15555550123")
    assert _matches_rule(msg, rule)


def test_sender_case_insensitive():
    msg = _make_msg(Subject="Hi", From="Alerts@Example.COM")
    rule = EmailWatchRule(sender="alerts@example.com", phone_number="+15555550123")
    assert _matches_rule(msg, rule)


def test_sender_mismatch():
    msg = _make_msg(Subject="Hi", From="other@example.com")
    rule = EmailWatchRule(sender="alerts@example.com", phone_number="+15555550123")
    assert not _matches_rule(msg, rule)


def test_both_match():
    msg = _make_msg(Subject="Order shipped", From="shop@example.com")
    rule = EmailWatchRule(
        subject="Order shipped",
        sender="shop@example.com",
        phone_number="+15555550123",
    )
    assert _matches_rule(msg, rule)


def test_subject_matches_sender_does_not():
    msg = _make_msg(Subject="Order shipped", From="other@example.com")
    rule = EmailWatchRule(
        subject="Order shipped",
        sender="shop@example.com",
        phone_number="+15555550123",
    )
    assert not _matches_rule(msg, rule)


def test_sender_matches_subject_does_not():
    msg = _make_msg(Subject="Order delayed", From="shop@example.com")
    rule = EmailWatchRule(
        subject="Order shipped",
        sender="shop@example.com",
        phone_number="+15555550123",
    )
    assert not _matches_rule(msg, rule)


# ---------------------------------------------------------------------------
# _check_mailbox integration (mocked IMAP)
# ---------------------------------------------------------------------------


def _raw_header(subject: str, sender: str) -> bytes:
    msg = MIMEText("")
    del msg["Subject"]
    del msg["From"]
    msg["Subject"] = subject
    msg["From"] = sender
    return msg.as_bytes()


def _make_config(rules: list[EmailWatchRule]) -> AppConfig:
    return AppConfig(
        pushbullet=PushbulletConfig(api_key="fake", device_iden="fake"),
        email_watch=EmailWatchConfig(
            enabled=True,
            imap_server="imap.example.com",
            email_address="test@example.com",
            app_password="secret",
            rules=rules,
        ),
    )


@patch("pushbullet_calend.email_monitor.send_sms")
@patch("pushbullet_calend.email_monitor.imaplib.IMAP4_SSL")
def test_subject_match_sends_sms(mock_imap_cls, mock_send, tmp_path):
    conn = MagicMock()
    mock_imap_cls.return_value = conn
    conn.search.return_value = ("OK", [b"1"])
    conn.fetch.return_value = ("OK", [(b"1", _raw_header("Alert!", "bot@x.com"))])

    rule = EmailWatchRule(subject="Alert!", phone_number="+15555550123", message="Got it")
    config = _make_config([rule])
    store = EmailNotificationStore(tmp_path / "test.db")

    count = _check_mailbox(config.email_watch, config, store)

    assert count == 1
    mock_send.assert_called_once()
    store.close()


@patch("pushbullet_calend.email_monitor.send_sms")
@patch("pushbullet_calend.email_monitor.imaplib.IMAP4_SSL")
def test_sender_match_sends_sms(mock_imap_cls, mock_send, tmp_path):
    conn = MagicMock()
    mock_imap_cls.return_value = conn
    conn.search.return_value = ("OK", [b"1"])
    conn.fetch.return_value = (
        "OK",
        [(b"1", _raw_header("Random subject", "alerts@example.com"))],
    )

    rule = EmailWatchRule(
        sender="alerts@example.com", phone_number="+15555550123", message="Alert!"
    )
    config = _make_config([rule])
    store = EmailNotificationStore(tmp_path / "test.db")

    count = _check_mailbox(config.email_watch, config, store)

    assert count == 1
    mock_send.assert_called_once()
    store.close()


@patch("pushbullet_calend.email_monitor.send_sms")
@patch("pushbullet_calend.email_monitor.imaplib.IMAP4_SSL")
def test_both_criteria_must_match(mock_imap_cls, mock_send, tmp_path):
    conn = MagicMock()
    mock_imap_cls.return_value = conn
    conn.search.return_value = ("OK", [b"1"])
    conn.fetch.return_value = (
        "OK",
        [(b"1", _raw_header("Alert!", "wrong@example.com"))],
    )

    rule = EmailWatchRule(
        subject="Alert!",
        sender="alerts@example.com",
        phone_number="+15555550123",
        message="Got it",
    )
    config = _make_config([rule])
    store = EmailNotificationStore(tmp_path / "test.db")

    count = _check_mailbox(config.email_watch, config, store)

    assert count == 0
    mock_send.assert_not_called()
    store.close()


@patch("pushbullet_calend.email_monitor.send_sms")
@patch("pushbullet_calend.email_monitor.imaplib.IMAP4_SSL")
def test_rule_with_no_subject_or_sender_is_skipped(mock_imap_cls, mock_send, tmp_path):
    conn = MagicMock()
    mock_imap_cls.return_value = conn

    rule = EmailWatchRule(phone_number="+15555550123", message="Empty rule")
    config = _make_config([rule])
    store = EmailNotificationStore(tmp_path / "test.db")

    count = _check_mailbox(config.email_watch, config, store)

    assert count == 0
    conn.search.assert_not_called()
    store.close()


@patch("pushbullet_calend.email_monitor.send_sms")
@patch("pushbullet_calend.email_monitor.imaplib.IMAP4_SSL")
def test_dedup_prevents_duplicate_sms(mock_imap_cls, mock_send, tmp_path):
    conn = MagicMock()
    mock_imap_cls.return_value = conn
    conn.search.return_value = ("OK", [b"1"])
    conn.fetch.return_value = ("OK", [(b"1", _raw_header("Alert!", "bot@x.com"))])

    rule = EmailWatchRule(subject="Alert!", phone_number="+15555550123", message="Got it")
    config = _make_config([rule])
    store = EmailNotificationStore(tmp_path / "test.db")

    _check_mailbox(config.email_watch, config, store)
    _check_mailbox(config.email_watch, config, store)

    assert mock_send.call_count == 1
    store.close()


# ---------------------------------------------------------------------------
# Coverage for remaining branches
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("=?utf-8?q?caf=C3=A9_open?=", "café open"),
        ("=?iso-8859-1?q?caf=E9?=", "café"),
        ("Re: =?utf-8?q?caf=C3=A9?=", "Re: café"),
    ],
    ids=["utf8", "latin1", "mixed_plain_and_encoded"],
)
def test_decode_header_encoded_words(raw, expected):
    assert _decode_header(_make_msg(Subject=raw), "Subject") == expected


def test_build_search_criteria_non_ascii_subject_uses_longest_ascii_run():
    rule = EmailWatchRule(subject="It's here… free product time!", phone_number="+15555550123")
    criteria = _build_search_criteria(rule, "09-Jun-2026")
    assert criteria[:2] == ["SUBJECT", '"free product time!"']


@pytest.mark.parametrize(
    "search_result, fetch_result, expected_sms",
    [
        (("NO", [None]), None, []),
        (("OK", [b"1"]), ("NO", [None]), []),
        (
            ("OK", [b"1"]),
            ("OK", [(b"1", b"Subject: Alert!\r\nFrom: bot@x.com\r\n\r\n")]),
            [call(ANY, "+15555550123", "Email alert: Alert!")],
        ),
    ],
    ids=["search_fails", "fetch_fails", "rule_without_message_uses_default"],
)
@patch("pushbullet_calend.email_monitor.send_sms")
@patch("pushbullet_calend.email_monitor.imaplib.IMAP4_SSL")
def test_imap_results(
    mock_imap_cls, mock_send, tmp_path, search_result, fetch_result, expected_sms
):
    conn = mock_imap_cls.return_value
    conn.search.return_value = search_result
    conn.fetch.return_value = fetch_result
    config = _make_config([EmailWatchRule(subject="Alert!", phone_number="+15555550123")])
    store = EmailNotificationStore(tmp_path / "test.db")

    _check_mailbox(config.email_watch, config, store)

    assert mock_send.call_args_list == expected_sms
    store.close()


@pytest.mark.parametrize(
    "connect_error, login_error",
    [(OSError("unreachable"), None), (None, imaplib.IMAP4.error("bad password"))],
    ids=["connect_fails", "login_fails"],
)
@patch("pushbullet_calend.email_monitor.send_sms")
@patch("pushbullet_calend.email_monitor.imaplib.IMAP4_SSL")
def test_connection_failure_sends_nothing(mock_imap_cls, mock_send, connect_error, login_error):
    mock_imap_cls.side_effect = connect_error
    mock_imap_cls.return_value.login.side_effect = login_error
    config = _make_config([EmailWatchRule(subject="Alert!", phone_number="+15555550123")])

    assert _check_mailbox(config.email_watch, config, None) == 0
    mock_send.assert_not_called()


@pytest.mark.parametrize(
    "email_watch",
    [
        EmailWatchConfig(
            enabled=False, rules=[EmailWatchRule(subject="Alert!", phone_number="+15555550123")]
        ),
        EmailWatchConfig(enabled=True),
    ],
    ids=["disabled", "no_rules"],
)
@patch("pushbullet_calend.email_monitor.imaplib.IMAP4_SSL")
def test_inactive_watch_does_not_connect(mock_imap_cls, email_watch):
    assert _check_mailbox(email_watch, AppConfig(), None) == 0
    mock_imap_cls.assert_not_called()


@pytest.mark.parametrize(
    "send_error, expected_notifications",
    [(TransientError("timeout"), 0), (PermanentError("bad number"), 1)],
    ids=["transient", "permanent"],
)
@patch("pushbullet_calend.email_monitor.notify_failure")
@patch("pushbullet_calend.email_monitor.send_sms")
@patch("pushbullet_calend.email_monitor.imaplib.IMAP4_SSL")
def test_send_errors(
    mock_imap_cls, mock_send, mock_notify, tmp_path, send_error, expected_notifications
):
    conn = mock_imap_cls.return_value
    conn.search.return_value = ("OK", [b"1"])
    conn.fetch.return_value = ("OK", [(b"1", _raw_header("Alert!", "bot@x.com"))])
    mock_send.side_effect = send_error
    config = _make_config([EmailWatchRule(subject="Alert!", phone_number="+15555550123")])
    store = EmailNotificationStore(tmp_path / "test.db")

    assert _check_mailbox(config.email_watch, config, store) == 0
    assert mock_notify.call_count == expected_notifications
    store.close()


@patch("pushbullet_calend.email_monitor.send_sms")
@patch("pushbullet_calend.email_monitor.imaplib.IMAP4_SSL")
def test_check_email_ignores_logout_errors(mock_imap_cls, mock_send, tmp_path):
    conn = mock_imap_cls.return_value
    conn.search.return_value = ("OK", [b"1"])
    conn.fetch.return_value = ("OK", [(b"1", _raw_header("Alert!", "bot@x.com"))])
    conn.close.side_effect = imaplib.IMAP4.error("already closed")
    store = EmailNotificationStore(tmp_path / "test.db")

    assert (
        check_email(
            _make_config([EmailWatchRule(subject="Alert!", phone_number="+15555550123")]), store
        )
        == 1
    )
    store.close()

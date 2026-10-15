"""Tests for pushbullet_calend.sender."""

import logging
from unittest.mock import MagicMock

import pytest
import requests

from pushbullet_calend import sender
from pushbullet_calend.config import PushbulletConfig
from pushbullet_calend.sender import PermanentError, TransientError, notify_failure, send_sms

_CONFIG = PushbulletConfig(api_key="o.test", device_iden="dev_123")


def test_send_sms_posts_text(monkeypatch):
    request = MagicMock()
    monkeypatch.setattr(requests, "request", request)

    send_sms(_CONFIG, "+15555550123", "Leave now")

    request.assert_called_once_with(
        "POST",
        "https://api.pushbullet.com/v2/texts",
        headers={"Access-Token": "o.test", "Content-Type": "application/json"},
        json={
            "data": {
                "addresses": ["+15555550123"],
                "guid": f"sms-+15555550123-{hash('Leave now')}",
                "target_device_iden": "dev_123",
                "message": "Leave now",
            }
        },
        timeout=30,
    )


@pytest.mark.parametrize(
    "responses, expected_attempts",
    [
        ([requests.ConnectionError(), requests.Timeout(), MagicMock()], 3),
        ([requests.HTTPError(response=MagicMock(status_code=503)), MagicMock()], 2),
    ],
    ids=["network_errors", "server_error"],
)
def test_send_sms_retries_transient_errors(monkeypatch, responses, expected_attempts):
    request = MagicMock(side_effect=responses)
    monkeypatch.setattr(requests, "request", request)
    monkeypatch.setattr(sender.time, "sleep", lambda seconds: None)

    send_sms(_CONFIG, "+15555550123", "Leave now")

    assert request.call_count == expected_attempts


@pytest.mark.parametrize(
    "responses, expected_error, expected_attempts",
    [
        ([requests.HTTPError(response=MagicMock(status_code=400))], PermanentError, 1),
        ([requests.HTTPError()], PermanentError, 1),
        ([requests.TooManyRedirects()], PermanentError, 1),
        ([requests.ConnectionError()] * 3, TransientError, 3),
    ],
    ids=["client_error", "http_error_without_response", "other_error", "retries_exhausted"],
)
def test_send_sms_raises(monkeypatch, responses, expected_error, expected_attempts):
    request = MagicMock(side_effect=responses)
    monkeypatch.setattr(requests, "request", request)
    monkeypatch.setattr(sender.time, "sleep", lambda seconds: None)

    with pytest.raises(expected_error):
        send_sms(_CONFIG, "+15555550123", "Leave now")

    assert request.call_count == expected_attempts


@pytest.mark.parametrize(
    "responses, expected_log",
    [
        ([MagicMock()], "Failure notification sent: Calendar fetch failed"),
        (
            [requests.HTTPError(response=MagicMock(status_code=401))],
            "Could not send failure notification: Calendar fetch failed",
        ),
    ],
    ids=["sent", "failure_logged_not_raised"],
)
def test_notify_failure(monkeypatch, caplog, responses, expected_log):
    caplog.set_level(logging.INFO)
    monkeypatch.setattr(requests, "request", MagicMock(side_effect=responses))

    notify_failure(_CONFIG, "Calendar fetch failed", "Check logs.")

    assert expected_log in caplog.text

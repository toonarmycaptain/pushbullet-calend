"""Pushbullet SMS sending and failure notifications."""

import logging
import time

import requests

from pushbullet_calend.config import PushbulletConfig

_log = logging.getLogger(__name__)

_API_BASE = "https://api.pushbullet.com/v2"
_MAX_RETRIES = 3
_BACKOFF_BASE = 2  # seconds


class SendError(Exception):
    """Raised when an SMS fails after all retries."""


def _headers(config: PushbulletConfig) -> dict[str, str]:
    return {
        "Access-Token": config.api_key,
        "Content-Type": "application/json",
    }


def _request_with_retry(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    json: dict,
    max_retries: int = _MAX_RETRIES,
) -> requests.Response:
    """Make an HTTP request with exponential backoff on failure."""
    last_exc: Exception | None = None
    for attempt in range(max_retries):
        try:
            resp = requests.request(method, url, headers=headers, json=json, timeout=30)
            resp.raise_for_status()
            return resp
        except requests.RequestException as exc:
            last_exc = exc
            if attempt < max_retries - 1:
                wait = _BACKOFF_BASE ** (attempt + 1)
                _log.warning(
                    "Request to %s failed (attempt %d/%d), retrying in %ds: %s",
                    url,
                    attempt + 1,
                    max_retries,
                    wait,
                    exc,
                )
                time.sleep(wait)
    raise last_exc  # type: ignore[misc]


def send_sms(
    config: PushbulletConfig,
    phone_number: str,
    message: str,
) -> None:
    """Send an SMS via Pushbullet. Raises SendError on failure."""
    payload = {
        "data": {
            "addresses": [phone_number],
            "guid": f"sms-{phone_number}-{hash(message)}",
            "target_device_iden": config.device_iden,
            "body": message,
        },
    }
    try:
        _request_with_retry(
            "POST",
            f"{_API_BASE}/texts",
            headers=_headers(config),
            json=payload,
        )
        _log.info("SMS sent to %s", phone_number)
    except requests.RequestException as exc:
        raise SendError(f"Failed to send SMS to {phone_number}: {exc}") from exc


def notify_failure(
    config: PushbulletConfig,
    title: str,
    body: str,
) -> None:
    """Send a push notification to self about a failure. Logs if this also fails."""
    payload = {
        "type": "note",
        "title": title,
        "body": body,
    }
    try:
        _request_with_retry(
            "POST",
            f"{_API_BASE}/pushes",
            headers=_headers(config),
            json=payload,
        )
        _log.info("Failure notification sent: %s", title)
    except requests.RequestException:
        _log.exception("Could not send failure notification: %s — %s", title, body)

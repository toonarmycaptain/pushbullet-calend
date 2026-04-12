"""Main entry point — poll calendars and send due SMS messages."""

import logging
from datetime import UTC, datetime

from pushbullet_calend.calendar_client import fetch_events
from pushbullet_calend.config import AppConfig, load_config
from pushbullet_calend.db import SentStore
from pushbullet_calend.parser import parse_directives
from pushbullet_calend.sender import PermanentError, TransientError, notify_failure, send_sms

_log = logging.getLogger(__name__)


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def run(config: AppConfig | None = None) -> None:
    """Single poll cycle: fetch events, send any due SMS messages."""
    if config is None:
        config = load_config()

    store = SentStore(config.database.path)
    now = datetime.now(UTC)

    try:
        events = fetch_events(
            config.google,
            lookahead_days=config.schedule.lookahead_days,
        )
    except Exception:
        _log.exception("Failed to fetch calendar events")
        notify_failure(
            config.pushbullet,
            "Calendar fetch failed",
            "Could not retrieve events from Google Calendar. Check logs.",
        )
        return

    sent_count = 0
    for event in events:
        directives = parse_directives(event.description)
        if directives:
            _log.info(
                "Event '%s' at %s has %d SMS directive(s)",
                event.summary,
                event.start,
                len(directives),
            )
        for directive in directives:
            send_time = event.start - directive.offset
            instance_start = event.start.isoformat()

            if not (send_time <= now < event.start):
                _log.debug(
                    "Not yet time for %s (send at %s, now %s)",
                    directive.phone_number,
                    send_time,
                    now,
                )
                continue

            if not store.should_send(
                event.event_id,
                instance_start,
                directive.phone_number,
                directive.message,
            ):
                continue

            try:
                send_sms(
                    config.pushbullet,
                    directive.phone_number,
                    directive.message,
                )
                store.record_sent(
                    event.event_id,
                    instance_start,
                    directive.phone_number,
                    directive.message,
                )
                sent_count += 1
            except TransientError as exc:
                # Network/server issue — don't record, just skip this cycle.
                # The message will be retried on the next poll.
                _log.warning(
                    "Transient error sending to %s, will retry next cycle: %s",
                    directive.phone_number,
                    exc,
                )
            except PermanentError as exc:
                _log.error("Permanent send failure to %s: %s", directive.phone_number, exc)
                retries = store.record_failure(
                    event.event_id,
                    instance_start,
                    directive.phone_number,
                    directive.message,
                )
                notify_failure(
                    config.pushbullet,
                    f"SMS to {directive.phone_number} failed ({retries}x)",
                    f"Event: {event.summary}\nMessage: {directive.message}\nError: {exc}",
                )

    _log.info("Poll complete: %d messages sent", sent_count)
    store.close()


def main() -> None:
    _configure_logging()
    run()


if __name__ == "__main__":
    main()

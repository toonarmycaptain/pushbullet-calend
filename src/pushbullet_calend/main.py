"""Main entry point — poll calendars and send due SMS messages."""

import logging
import signal
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pushbullet_calend.calendar_client import CalendarEvent, fetch_events
from pushbullet_calend.config import AppConfig, load_config
from pushbullet_calend.db import SentStore
from pushbullet_calend.parser import SmsDirective, parse_directives
from pushbullet_calend.sender import PermanentError, TransientError, notify_failure, send_sms

logger = logging.getLogger(__name__)


@dataclass
class PendingSms:
    event: CalendarEvent
    directive: SmsDirective
    send_time: datetime


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _collect_pending(
    events: list[CalendarEvent],
    store: SentStore,
) -> list[PendingSms]:
    """Build a list of SMS messages that are pending (not yet sent, not expired)."""
    pending: list[PendingSms] = []
    for event in events:
        directives = parse_directives(event.description)
        if directives:
            logger.info(
                "Event '%s' at %s has %d SMS directive(s)",
                event.summary,
                event.start,
                len(directives),
            )
        for directive in directives:
            send_time = event.start - directive.offset
            instance_start = event.start.isoformat()

            if send_time >= event.start:
                continue

            if not store.should_send(
                event.event_id,
                instance_start,
                directive.phone_number,
                directive.message,
            ):
                continue

            pending.append(PendingSms(event=event, directive=directive, send_time=send_time))
    return pending


def _send_due(pending: list[PendingSms], config: AppConfig, store: SentStore) -> int:
    """Send any messages whose send_time has arrived. Returns count sent."""
    now = datetime.now(UTC)
    sent_count = 0
    for item in pending:
        if not (item.send_time <= now < item.event.start):
            continue

        instance_start = item.event.start.isoformat()
        try:
            send_sms(config.pushbullet, item.directive.phone_number, item.directive.message)
            store.record_sent(
                item.event.event_id,
                instance_start,
                item.directive.phone_number,
                item.directive.message,
            )
            sent_count += 1
        except TransientError as exc:
            logger.warning(
                "Transient error sending to %s, will retry: %s",
                item.directive.phone_number,
                exc,
            )
        except PermanentError as exc:
            logger.error("Permanent send failure to %s: %s", item.directive.phone_number, exc)
            retries = store.record_failure(
                item.event.event_id,
                instance_start,
                item.directive.phone_number,
                item.directive.message,
            )
            notify_failure(
                config.pushbullet,
                f"SMS to {item.directive.phone_number} failed ({retries}x)",
                f"Event: {item.event.summary}\nMessage: {item.directive.message}\nError: {exc}",
            )
    return sent_count


def run_once(config: AppConfig | None = None) -> None:
    """Single poll cycle: fetch events, send any due SMS messages."""
    if config is None:
        config = load_config()

    store = SentStore(config.database.path)
    try:
        events = fetch_events(
            config.google,
            lookahead_days=config.schedule.lookahead_days,
        )
    except Exception:
        logger.exception("Failed to fetch calendar events")
        notify_failure(
            config.pushbullet,
            "Calendar fetch failed",
            "Could not retrieve events from Google Calendar. Check logs.",
        )
        return

    pending = _collect_pending(events, store)
    sent_count = _send_due(pending, config, store)
    logger.info("Poll complete: %d messages sent", sent_count)
    store.close()


def run_daemon(config: AppConfig | None = None) -> None:
    """Run as a background daemon, polling and sleeping until exact send times."""
    if config is None:
        config = load_config()

    running = True

    def _handle_signal(signum: int, _frame: object) -> None:
        nonlocal running
        logger.info("Received signal %d, shutting down", signum)
        running = False

    signal.signal(signal.SIGINT, _handle_signal)
    signal.signal(signal.SIGTERM, _handle_signal)

    poll_interval = timedelta(minutes=config.schedule.poll_interval_minutes)
    store = SentStore(config.database.path)
    logger.info("Daemon started, polling every %s", poll_interval)

    pending: list[PendingSms] = []
    next_poll = datetime.now(UTC)

    while running:
        now = datetime.now(UTC)

        # Time to re-poll the calendar?
        if now >= next_poll:
            try:
                events = fetch_events(
                    config.google,
                    lookahead_days=config.schedule.lookahead_days,
                )
                pending = _collect_pending(events, store)
                logger.info(
                    "Polled calendar: %d events, %d pending SMS",
                    len(events),
                    len(pending),
                )
            except Exception:
                logger.exception("Failed to fetch calendar events")
                notify_failure(
                    config.pushbullet,
                    "Calendar fetch failed",
                    "Could not retrieve events from Google Calendar. Check logs.",
                )
            next_poll = now + poll_interval

        # Send anything that's due right now
        sent_count = _send_due(pending, config, store)
        if sent_count:
            logger.info("Sent %d messages", sent_count)
            # Re-collect to remove sent items
            pending = [
                p
                for p in pending
                if store.should_send(
                    p.event.event_id,
                    p.event.start.isoformat(),
                    p.directive.phone_number,
                    p.directive.message,
                )
            ]

        # Sleep until the earliest of: next send time, or next poll
        now = datetime.now(UTC)
        wake_times = [next_poll]
        for item in pending:
            if item.send_time > now:
                wake_times.append(item.send_time)

        next_wake = min(wake_times)
        sleep_seconds = max(0, (next_wake - now).total_seconds())

        if sleep_seconds > 0:
            logger.debug("Sleeping %.0fs until %s", sleep_seconds, next_wake)
            # Sleep in small increments so we can respond to signals
            end = time.monotonic() + sleep_seconds
            while running and time.monotonic() < end:
                time.sleep(min(1.0, end - time.monotonic()))

    logger.info("Daemon stopped")
    store.close()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Pushbullet Calendar SMS Reminders")
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run as background daemon",
    )
    args = parser.parse_args()

    _configure_logging()

    if args.daemon:
        run_daemon()
    else:
        run_once()


if __name__ == "__main__":
    main()

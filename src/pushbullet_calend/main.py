"""Main entry point — poll calendars and send due SMS messages."""

import logging
import signal
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pushbullet_calend.calendar_client import CalendarEvent, fetch_events
from pushbullet_calend.config import AppConfig, load_config
from pushbullet_calend.db import EmailNotificationStore, SentStore
from pushbullet_calend.email_monitor import check_email
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


def _send_due(
    pending: list[PendingSms],
    config: AppConfig,
    store: SentStore,
    *,
    verify: bool = False,
) -> int:
    """Send any messages whose send_time has arrived. Returns count sent.

    If verify is True, re-fetch events from the calendar before sending
    to confirm they haven't been cancelled or moved.
    """
    now = datetime.now(UTC)
    due = [item for item in pending if item.send_time <= now < item.event.start]

    if not due:
        return 0

    # Re-fetch events to verify they still exist and haven't changed
    if verify:
        try:
            fresh_events = fetch_events(
                config.google,
                lookahead_days=config.schedule.lookahead_days,
            )
            # Build a set of (event_id, start_iso, description) for quick lookup
            fresh_lookup = {(e.event_id, e.start.isoformat()): e.description for e in fresh_events}
        except Exception:
            logger.exception("Failed to verify events before sending, skipping this cycle")
            return 0

    sent_count = 0
    for item in due:
        instance_start = item.event.start.isoformat()

        if verify:
            fresh_desc = fresh_lookup.get((item.event.event_id, instance_start))
            if fresh_desc is None:
                logger.info(
                    "Event '%s' at %s no longer exists, skipping SMS to %s",
                    item.event.summary,
                    item.event.start,
                    item.directive.phone_number,
                )
                continue
            # Re-parse and check the directive is still present
            fresh_directives = parse_directives(fresh_desc)
            still_valid = any(
                d.phone_number == item.directive.phone_number
                and d.message == item.directive.message
                for d in fresh_directives
            )
            if not still_valid:
                logger.info(
                    "SMS directive for %s in event '%s' was removed or changed, skipping",
                    item.directive.phone_number,
                    item.event.summary,
                )
                continue

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

    # Check email watches
    if config.email_watch.enabled:
        email_store = EmailNotificationStore(config.database.path)
        email_sent = check_email(config, email_store)
        if email_sent:
            logger.info("Email watch: %d SMS alerts sent", email_sent)
        email_store.close()


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
    email_store = (
        EmailNotificationStore(config.database.path) if config.email_watch.enabled else None
    )
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
            # Check email watches during each poll
            if email_store is not None:
                try:
                    email_sent = check_email(config, email_store)
                    if email_sent:
                        logger.info("Email watch: %d SMS alerts sent", email_sent)
                except Exception:
                    logger.exception("Failed to check email")

            next_poll = now + poll_interval

        # Send anything that's due — verify against live calendar data first
        sent_count = _send_due(pending, config, store, verify=True)
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
    if email_store is not None:
        email_store.close()


def _encrypt_password() -> None:
    """Interactive: encrypt a password and print the token for config.toml."""
    import getpass

    from pushbullet_calend.crypto import _DEFAULT_KEY_PATH, encrypt, generate_key, load_key

    if _DEFAULT_KEY_PATH.exists():
        key = load_key()
        print(f"Using existing key at {_DEFAULT_KEY_PATH}")
    else:
        key = generate_key()
        print(f"Generated new key at {_DEFAULT_KEY_PATH}")

    password = getpass.getpass("Enter the email password to encrypt: ")
    token = encrypt(password, key)
    print(f'\nPut this in your config.toml as app_password:\n\n  app_password = "{token}"\n')


def _test_email() -> None:
    """Test IMAP login and search without sending any SMS."""
    import email as email_mod
    import imaplib
    from email.header import decode_header

    config = load_config()
    ew = config.email_watch

    if not ew.enabled:
        print("email_watch is not enabled in config.toml")
        return

    print(f"Connecting to {ew.imap_server} as {ew.email_address}...")
    try:
        conn = imaplib.IMAP4_SSL(ew.imap_server)
        conn.login(ew.email_address, ew.app_password)
        print("Login successful!")
    except Exception as exc:
        print(f"Login FAILED: {exc}")
        return

    try:
        conn.select("INBOX", readonly=True)
        for rule in ew.rules:
            print(f'\nSearching for subject: "{rule.subject}"')
            ascii_words = rule.subject.encode("ascii", errors="replace").decode()
            runs = [r.strip() for r in ascii_words.split("?") if r.strip()]
            search_term = max(runs, key=len) if runs else rule.subject
            from datetime import timedelta

            since_date = (datetime.now(UTC) - timedelta(hours=12)).strftime("%d-%b-%Y")
            status, data = conn.search(None, "SUBJECT", f'"{search_term}"', "SINCE", since_date)
            if status != "OK" or not data[0]:
                print("  No matching emails found.")
                continue
            uids = data[0].split()
            print(f"  Found {len(uids)} matching email(s):")
            for uid in uids[:5]:  # Show at most 5
                status, msg_data = conn.fetch(uid, "(RFC822.HEADER)")
                if status == "OK":
                    msg = email_mod.message_from_bytes(msg_data[0][1])
                    raw_subj = msg.get("Subject", "")
                    parts = decode_header(raw_subj)
                    subject = "".join(
                        p.decode(c or "utf-8", errors="replace") if isinstance(p, bytes) else p
                        for p, c in parts
                    )
                    print(f"    UID {uid.decode()}: {subject}")
            if len(uids) > 5:
                print(f"    ... and {len(uids) - 5} more")
    finally:
        conn.close()
        conn.logout()

    print("\nEmail check test complete. No SMS was sent.")


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Pushbullet Calendar SMS Reminders")
    parser.add_argument(
        "--daemon",
        action="store_true",
        help="Run as background daemon",
    )
    parser.add_argument(
        "--encrypt-password",
        action="store_true",
        help="Encrypt a password for config.toml",
    )
    parser.add_argument(
        "--test-email",
        action="store_true",
        help="Test email login and search without sending SMS",
    )
    args = parser.parse_args()

    _configure_logging()

    if args.encrypt_password:
        _encrypt_password()
    elif args.test_email:
        _test_email()
    elif args.daemon:
        run_daemon()
    else:
        run_once()


if __name__ == "__main__":
    main()

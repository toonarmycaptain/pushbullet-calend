"""Monitor email inboxes via IMAP and trigger SMS notifications."""

import email
import imaplib
import logging
from datetime import UTC, datetime, timedelta
from email.header import decode_header

from pushbullet_calend.config import AppConfig, EmailWatchConfig
from pushbullet_calend.db import EmailNotificationStore
from pushbullet_calend.sender import PermanentError, TransientError, notify_failure, send_sms

logger = logging.getLogger(__name__)


def _decode_subject(msg: email.message.Message) -> str:
    """Decode a possibly-encoded email subject header."""
    raw = msg.get("Subject", "")
    parts = decode_header(raw)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def _check_mailbox(
    email_config: EmailWatchConfig,
    config: AppConfig,
    email_store: EmailNotificationStore,
) -> int:
    """Connect to IMAP, search for matching subjects, send SMS. Returns count sent."""
    if not email_config.enabled or not email_config.rules:
        return 0

    sent_count = 0
    try:
        conn = imaplib.IMAP4_SSL(email_config.imap_server)
        conn.login(email_config.email_address, email_config.app_password)
    except Exception:
        logger.exception("Failed to connect to IMAP server %s", email_config.imap_server)
        return 0

    try:
        conn.select("INBOX", readonly=True)

        for rule in email_config.rules:
            # Search for emails with matching subject
            # IMAP SEARCH uses substring matching for SUBJECT
            # Use longest ASCII-safe word sequence for IMAP search, verify full match later
            ascii_words = rule.subject.encode("ascii", errors="replace").decode()
            # Find longest run of clean words (no replacement chars)
            runs = [r.strip() for r in ascii_words.split("?") if r.strip()]
            search_term = max(runs, key=len) if runs else rule.subject
            since_date = (datetime.now(UTC) - timedelta(hours=12)).strftime("%d-%b-%Y")
            status, data = conn.search(None, "SUBJECT", f'"{search_term}"', "SINCE", since_date)
            if status != "OK" or not data[0]:
                continue

            uids = data[0].split()
            for uid in uids:
                uid_str = uid.decode()

                if not email_store.should_notify(
                    email_config.email_address, uid_str, rule.subject
                ):
                    continue

                # Fetch the email to verify subject is an exact match
                status, msg_data = conn.fetch(uid, "(RFC822.HEADER)")
                if status != "OK":
                    continue

                raw_header = msg_data[0][1]
                msg = email.message_from_bytes(raw_header)
                subject = _decode_subject(msg)

                if subject != rule.subject:
                    continue

                sms_body = rule.message or f"Email alert: {subject}"
                logger.info(
                    "Email match: '%s' — texting %s",
                    subject,
                    rule.phone_number,
                )

                try:
                    send_sms(config.pushbullet, rule.phone_number, sms_body)
                    email_store.record_notified(email_config.email_address, uid_str, rule.subject)
                    sent_count += 1
                except TransientError as exc:
                    logger.warning(
                        "Transient error sending email alert SMS to %s: %s",
                        rule.phone_number,
                        exc,
                    )
                except PermanentError as exc:
                    logger.error(
                        "Permanent error sending email alert SMS to %s: %s",
                        rule.phone_number,
                        exc,
                    )
                    notify_failure(
                        config.pushbullet,
                        f"Email alert SMS to {rule.phone_number} failed",
                        f"Subject: {subject}\nError: {exc}",
                    )
    finally:
        try:
            conn.close()
            conn.logout()
        except Exception:
            pass

    return sent_count


def check_email(config: AppConfig, email_store: EmailNotificationStore) -> int:
    """Check email and send SMS for matching messages. Returns count sent."""
    return _check_mailbox(config.email_watch, config, email_store)

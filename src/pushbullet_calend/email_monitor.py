"""Monitor email inboxes via IMAP and trigger SMS notifications."""

import email
import imaplib
import logging
from datetime import UTC, datetime, timedelta
from email.header import decode_header

from pushbullet_calend.config import AppConfig, EmailWatchConfig, EmailWatchRule
from pushbullet_calend.db import EmailNotificationStore
from pushbullet_calend.sender import PermanentError, TransientError, notify_failure, send_sms

logger = logging.getLogger(__name__)


def _decode_header(msg: email.message.Message, header: str) -> str:
    """Decode a possibly-encoded email header."""
    raw = msg.get(header, "")
    parts = decode_header(raw)
    decoded = []
    for part, charset in parts:
        if isinstance(part, bytes):
            decoded.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            decoded.append(part)
    return "".join(decoded)


def _rule_key(rule: EmailWatchRule) -> str:
    """Build a dedup key from the rule's match criteria."""
    if rule.subject and rule.sender:
        return f"{rule.subject}|from:{rule.sender}"
    if rule.sender:
        return f"from:{rule.sender}"
    return rule.subject


def _build_search_criteria(rule: EmailWatchRule, since_date: str) -> list[str]:
    """Build IMAP SEARCH criteria from a rule."""
    criteria: list[str] = []
    if rule.subject:
        ascii_words = rule.subject.encode("ascii", errors="replace").decode()
        runs = [r.strip() for r in ascii_words.split("?") if r.strip()]
        search_term = max(runs, key=len) if runs else rule.subject
        criteria.extend(["SUBJECT", f'"{search_term}"'])
    if rule.sender:
        criteria.extend(["FROM", f'"{rule.sender}"'])
    criteria.extend(["SINCE", since_date])
    return criteria


def _matches_rule(msg: email.message.Message, rule: EmailWatchRule) -> bool:
    """Check whether fetched headers satisfy the rule's criteria."""
    if rule.subject:
        subject = _decode_header(msg, "Subject")
        if subject != rule.subject:
            return False
    if rule.sender:
        from_header = _decode_header(msg, "From")
        if rule.sender.lower() not in from_header.lower():
            return False
    return True


def _check_mailbox(
    email_config: EmailWatchConfig,
    config: AppConfig,
    email_store: EmailNotificationStore,
) -> int:
    """Connect to IMAP, search for matching emails, send SMS. Returns count sent."""
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
            if not rule.subject and not rule.sender:
                logger.warning("Skipping email rule with no subject or sender")
                continue

            since_date = (datetime.now(UTC) - timedelta(hours=12)).strftime("%d-%b-%Y")
            criteria = _build_search_criteria(rule, since_date)
            status, data = conn.search(None, *criteria)
            if status != "OK" or not data[0]:
                continue

            key = _rule_key(rule)
            uids = data[0].split()
            for uid in uids:
                uid_str = uid.decode()

                if not email_store.should_notify(email_config.email_address, uid_str, key):
                    continue

                status, msg_data = conn.fetch(uid, "(RFC822.HEADER)")
                if status != "OK":
                    continue

                raw_header = msg_data[0][1]
                msg = email.message_from_bytes(raw_header)

                if not _matches_rule(msg, rule):
                    continue

                subject = _decode_header(msg, "Subject")
                sms_body = rule.message or f"Email alert: {subject}"
                match_desc = subject or _decode_header(msg, "From")
                logger.info(
                    "Email match: '%s' — texting %s",
                    match_desc,
                    rule.phone_number,
                )

                try:
                    send_sms(config.pushbullet, rule.phone_number, sms_body)
                    email_store.record_notified(email_config.email_address, uid_str, key)
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
                        f"Match: {match_desc}\nError: {exc}",
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

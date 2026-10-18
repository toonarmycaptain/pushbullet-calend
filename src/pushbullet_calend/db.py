"""SQLite storage for tracking sent SMS messages."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Integer, String, UniqueConstraint, create_engine, inspect, select, text
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

_MAX_RETRIES = 5


class Base(DeclarativeBase):
    pass


class NotifiedEmail(Base):
    __tablename__ = "notified_emails"
    __table_args__ = (
        UniqueConstraint(
            "email_account",
            "email_uid",
            "rule_subject",
            name="uq_email_dedup",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    email_account: Mapped[str] = mapped_column(String, nullable=False)
    email_uid: Mapped[str] = mapped_column(String, nullable=False)
    rule_subject: Mapped[str] = mapped_column(String, nullable=False)
    notified_at: Mapped[str] = mapped_column(String, nullable=False)


class SentMessage(Base):
    __tablename__ = "sent_messages"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "instance_start",
            "phone_number",
            "message_hash",
            name="uq_dedup",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    event_id: Mapped[str] = mapped_column(String, nullable=False)
    instance_start: Mapped[str] = mapped_column(String, nullable=False)
    phone_number: Mapped[str] = mapped_column(String, nullable=False)
    message_hash: Mapped[str] = mapped_column(String, nullable=False)
    sent_at: Mapped[str] = mapped_column(String, nullable=False)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    retry_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    # When the text was due (UTC), so the same text isn't sent again from another event.
    send_time: Mapped[str | None] = mapped_column(String, nullable=True)


def message_hash(text: str) -> str:
    """Return a short hash of *text* for dedup purposes."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def _utc_iso(moment: datetime) -> str:
    """Return *moment* as a UTC ISO string, so equal instants compare equal."""
    return moment.astimezone(UTC).isoformat()


class SentStore:
    """Tracks which SMS messages have already been sent."""

    def __init__(self, db_path: str | Path, *, max_retries: int = _MAX_RETRIES) -> None:
        self._engine = create_engine(f"sqlite:///{db_path}")
        self._max_retries = max_retries
        Base.metadata.create_all(self._engine)
        # Databases from before send_time existed get the column added in place.
        columns = {c["name"] for c in inspect(self._engine).get_columns("sent_messages")}
        if "send_time" not in columns:
            with self._engine.begin() as conn:
                conn.execute(text("ALTER TABLE sent_messages ADD COLUMN send_time VARCHAR"))

    def close(self) -> None:
        self._engine.dispose()

    def _find(
        self,
        session: Session,
        event_id: str,
        instance_start: str,
        phone_number: str,
        msg_hash: str,
    ) -> SentMessage | None:
        return session.execute(
            select(SentMessage).where(
                SentMessage.event_id == event_id,
                SentMessage.instance_start == instance_start,
                SentMessage.phone_number == phone_number,
                SentMessage.message_hash == msg_hash,
            )
        ).scalar_one_or_none()

    def should_send(
        self,
        event_id: str,
        instance_start: str,
        phone_number: str,
        message: str,
        *,
        send_time: datetime | None = None,
    ) -> bool:
        """Return True if this message should be sent.

        True when: no record exists, or status is 'failed' with retries remaining.
        False when: status is 'sent', or retries are exhausted. Given *send_time*,
        also False when the same message already went to the same number for that
        send time from any event, so copies of an event never text twice.
        """
        with Session(self._engine) as session:
            if (
                send_time is not None
                and session.execute(
                    select(SentMessage.id).where(
                        SentMessage.phone_number == phone_number,
                        SentMessage.message_hash == message_hash(message),
                        SentMessage.send_time == _utc_iso(send_time),
                        SentMessage.status == "sent",
                    )
                ).first()
            ):
                return False
            row = self._find(
                session,
                event_id,
                instance_start,
                phone_number,
                message_hash(message),
            )
            if row is None:
                return True
            if row.status == "sent":
                return False
            return row.retry_count < self._max_retries

    def record_sent(
        self,
        event_id: str,
        instance_start: str,
        phone_number: str,
        message: str,
        *,
        send_time: datetime | None = None,
    ) -> None:
        """Record a successful send."""
        mh = message_hash(message)
        due = _utc_iso(send_time) if send_time is not None else None
        with Session(self._engine) as session:
            row = self._find(session, event_id, instance_start, phone_number, mh)
            if row is None:
                session.add(
                    SentMessage(
                        event_id=event_id,
                        instance_start=instance_start,
                        phone_number=phone_number,
                        message_hash=mh,
                        sent_at=datetime.now(UTC).isoformat(),
                        status="sent",
                        send_time=due,
                    )
                )
            else:
                row.status = "sent"
                row.sent_at = datetime.now(UTC).isoformat()
                row.send_time = due
            session.commit()

    def record_failure(
        self,
        event_id: str,
        instance_start: str,
        phone_number: str,
        message: str,
    ) -> int:
        """Record a failed send attempt. Returns the new retry count."""
        mh = message_hash(message)
        with Session(self._engine) as session:
            row = self._find(session, event_id, instance_start, phone_number, mh)
            if row is None:
                session.add(
                    SentMessage(
                        event_id=event_id,
                        instance_start=instance_start,
                        phone_number=phone_number,
                        message_hash=mh,
                        sent_at=datetime.now(UTC).isoformat(),
                        status="failed",
                        retry_count=1,
                    )
                )
                session.commit()
                return 1
            row.retry_count += 1
            row.sent_at = datetime.now(UTC).isoformat()
            session.commit()
            return row.retry_count


class EmailNotificationStore:
    """Tracks which email UIDs have already triggered an SMS notification."""

    def __init__(self, db_path: str | Path) -> None:
        self._engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(self._engine)

    def close(self) -> None:
        self._engine.dispose()

    def should_notify(
        self,
        email_account: str,
        email_uid: str,
        rule_subject: str,
    ) -> bool:
        """Return True if this email UID hasn't been notified yet."""
        with Session(self._engine) as session:
            row = session.execute(
                select(NotifiedEmail).where(
                    NotifiedEmail.email_account == email_account,
                    NotifiedEmail.email_uid == email_uid,
                    NotifiedEmail.rule_subject == rule_subject,
                )
            ).scalar_one_or_none()
            return row is None

    def record_notified(
        self,
        email_account: str,
        email_uid: str,
        rule_subject: str,
    ) -> None:
        """Record that we sent a notification for this email."""
        with Session(self._engine) as session:
            session.add(
                NotifiedEmail(
                    email_account=email_account,
                    email_uid=email_uid,
                    rule_subject=rule_subject,
                    notified_at=datetime.now(UTC).isoformat(),
                )
            )
            session.commit()

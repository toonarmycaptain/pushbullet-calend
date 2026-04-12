"""SQLite storage for tracking sent SMS messages."""

import hashlib
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import Integer, String, UniqueConstraint, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column

_MAX_RETRIES = 5


class Base(DeclarativeBase):
    pass


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


def message_hash(text: str) -> str:
    """Return a short hash of *text* for dedup purposes."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class SentStore:
    """Tracks which SMS messages have already been sent."""

    def __init__(self, db_path: str | Path, *, max_retries: int = _MAX_RETRIES) -> None:
        self._engine = create_engine(f"sqlite:///{db_path}")
        self._max_retries = max_retries
        Base.metadata.create_all(self._engine)

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
    ) -> bool:
        """Return True if this message should be sent.

        True when: no record exists, or status is 'failed' with retries remaining.
        False when: status is 'sent', or retries are exhausted.
        """
        with Session(self._engine) as session:
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
    ) -> None:
        """Record a successful send."""
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
                        status="sent",
                    )
                )
            else:
                row.status = "sent"
                row.sent_at = datetime.now(UTC).isoformat()
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

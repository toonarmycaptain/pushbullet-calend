"""SQLite storage for tracking sent SMS messages."""

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import String, UniqueConstraint, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


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
    status: Mapped[str] = mapped_column(String, nullable=False, default="sent")


def message_hash(text: str) -> str:
    """Return a short hash of *text* for dedup purposes."""
    return hashlib.sha256(text.encode()).hexdigest()[:16]


class SentStore:
    """Tracks which SMS messages have already been sent."""

    def __init__(self, db_path: str | Path) -> None:
        self._engine = create_engine(f"sqlite:///{db_path}")
        Base.metadata.create_all(self._engine)

    def close(self) -> None:
        self._engine.dispose()

    def was_sent(
        self,
        event_id: str,
        instance_start: str,
        phone_number: str,
        message: str,
    ) -> bool:
        """Return True if this exact message was already sent."""
        stmt = select(SentMessage).where(
            SentMessage.event_id == event_id,
            SentMessage.instance_start == instance_start,
            SentMessage.phone_number == phone_number,
            SentMessage.message_hash == message_hash(message),
        )
        with Session(self._engine) as session:
            return session.execute(stmt).first() is not None

    def record_sent(
        self,
        event_id: str,
        instance_start: str,
        phone_number: str,
        message: str,
        *,
        status: str = "sent",
    ) -> None:
        """Record that a message was sent (or failed)."""
        with Session(self._engine) as session:
            existing = session.execute(
                select(SentMessage).where(
                    SentMessage.event_id == event_id,
                    SentMessage.instance_start == instance_start,
                    SentMessage.phone_number == phone_number,
                    SentMessage.message_hash == message_hash(message),
                )
            ).first()
            if existing is None:
                session.add(
                    SentMessage(
                        event_id=event_id,
                        instance_start=instance_start,
                        phone_number=phone_number,
                        message_hash=message_hash(message),
                        sent_at=datetime.now(timezone.utc).isoformat(),
                        status=status,
                    )
                )
                session.commit()

from __future__ import annotations

import enum
import uuid
from datetime import UTC, datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class ReminderChannel(enum.StrEnum):
    email = "email"
    slack = "slack"


class User(Base):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(200))
    password_hash: Mapped[str | None] = mapped_column(String(255), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    meeting_series: Mapped[list[MeetingSeries]] = relationship(back_populates="owner")
    external_identities: Mapped[list[ExternalIdentity]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class ExternalIdentity(Base):
    __tablename__ = "external_identities"
    __table_args__ = (
        UniqueConstraint("provider", "subject", name="uq_external_identity_provider_subject"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)

    # Identity provider (e.g. "google", "okta", "azuread", "saml")
    provider: Mapped[str] = mapped_column(String(50), index=True)
    # Provider subject / NameID / sub
    subject: Mapped[str] = mapped_column(String(255))

    # Optional cached attributes
    email: Mapped[str | None] = mapped_column(String(320), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    user: Mapped[User] = relationship(back_populates="external_identities")


class MeetingSeries(Base):
    __tablename__ = "meeting_series"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    owner_user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"), index=True)

    title: Mapped[str] = mapped_column(String(200))
    default_interval_days: Mapped[int] = mapped_column(Integer, default=7)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    owner: Mapped[User] = relationship(back_populates="meeting_series")
    occurrences: Mapped[list[MeetingOccurrence]] = relationship(
        back_populates="series", cascade="all, delete-orphan"
    )
    tasks: Mapped[list[Task]] = relationship(back_populates="series", cascade="all, delete-orphan")


class MeetingOccurrence(Base):
    __tablename__ = "meeting_occurrence"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    series_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meeting_series.id"), index=True)

    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    series: Mapped[MeetingSeries] = relationship(back_populates="occurrences")
    agenda_items: Mapped[list[AgendaItem]] = relationship(
        back_populates="occurrence", cascade="all, delete-orphan"
    )
    reminders: Mapped[list[Reminder]] = relationship(
        back_populates="occurrence", cascade="all, delete-orphan"
    )


class AgendaItem(Base):
    __tablename__ = "agenda_item"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    occurrence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meeting_occurrence.id"), index=True
    )

    body: Mapped[str] = mapped_column(Text)
    is_done: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    occurrence: Mapped[MeetingOccurrence] = relationship(back_populates="agenda_items")


class Task(Base):
    __tablename__ = "task"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    series_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("meeting_series.id"), index=True)

    title: Mapped[str] = mapped_column(String(300))
    is_done: Mapped[bool] = mapped_column(Boolean, default=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )

    series: Mapped[MeetingSeries] = relationship(back_populates="tasks")


class Reminder(Base):
    __tablename__ = "reminder"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)
    occurrence_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("meeting_occurrence.id"), index=True
    )

    channel: Mapped[ReminderChannel] = mapped_column(Enum(ReminderChannel))
    send_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    occurrence: Mapped[MeetingOccurrence] = relationship(back_populates="reminders")

"""Tables of the rate-shop store (docs/DESIGN.md, section 5).

Portable across PostgreSQL and SQLite. `rate_observations` is append-only: migration 0001 adds triggers that reject
UPDATE and DELETE on it in both databases.

Not here yet: `jobs`, `job_log`, `canary_results` and `alerts_sent`, which belong to the phase 2 worker.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    MetaData,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}

# SQLite only autoincrements an INTEGER PRIMARY KEY, so the big key falls back to INTEGER there.
BigId = BigInteger().with_variant(Integer(), "sqlite")
Money = Numeric(12, 2, asdecimal=False)

ROLES = ("target", "comp")
TASKS = ("calendar", "offers")
FETCH_OUTCOMES = ("pending", "ok", "blocked", "not_found", "parse_error", "failed")
RUN_STATUSES = ("running", "succeeded", "partial", "failed")


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class Hotel(Base):
    __tablename__ = "hotels"
    __table_args__ = (UniqueConstraint("collector", "source_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    collector: Mapped[str] = mapped_column(String(40))
    source_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    address: Mapped[str | None] = mapped_column(String(500))
    city: Mapped[str | None] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(80))
    star_class: Mapped[int | None] = mapped_column(Integer)
    rating: Mapped[float | None] = mapped_column(Float)
    reviews_count: Mapped[int | None] = mapped_column(Integer)
    latitude: Mapped[float | None] = mapped_column(Float)
    longitude: Mapped[float | None] = mapped_column(Float)
    google_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Study(Base):
    __tablename__ = "studies"
    __table_args__ = (CheckConstraint("schedule IN ('none', 'daily', 'weekly')", name="schedule"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(200), unique=True)
    city: Mapped[str] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(80))
    currency: Mapped[str] = mapped_column(String(3))
    adults: Mapped[int] = mapped_column(Integer, default=2)
    calendar_days: Mapped[int] = mapped_column(Integer, default=365)
    calendar_nights: Mapped[list[int]] = mapped_column(JSON, default=lambda: [1, 2])
    offers_nights: Mapped[int] = mapped_column(Integer, default=1)
    offers_months: Mapped[int] = mapped_column(Integer, default=12)
    schedule: Mapped[str] = mapped_column(String(10), default="none")
    schedule_end_date: Mapped[date | None] = mapped_column(Date)
    collector_location: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="active")
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    members: Mapped[list[StudyHotel]] = relationship(back_populates="study", cascade="all, delete-orphan")


class StudyHotel(Base):
    __tablename__ = "study_hotels"
    __table_args__ = (CheckConstraint("role IN ('target', 'comp')", name="role"),)

    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"), primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"), primary_key=True)
    role: Mapped[str] = mapped_column(String(10))
    input_name: Mapped[str | None] = mapped_column(String(255))
    confirmed_by: Mapped[str | None] = mapped_column(String(200))
    confirmed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    study: Mapped[Study] = relationship(back_populates="members")
    hotel: Mapped[Hotel] = relationship()


class Event(Base):
    __tablename__ = "events"
    __table_args__ = (UniqueConstraint("city", "event_date", "name"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    city: Mapped[str] = mapped_column(String(120))
    country: Mapped[str | None] = mapped_column(String(80))
    event_date: Mapped[date] = mapped_column(Date)
    end_date: Mapped[date | None] = mapped_column(Date)
    name: Mapped[str] = mapped_column(String(255))
    created_by: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class Run(Base):
    __tablename__ = "runs"
    __table_args__ = (CheckConstraint("status IN ('running', 'succeeded', 'partial', 'failed')", name="status"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    study_id: Mapped[int] = mapped_column(ForeignKey("studies.id", ondelete="CASCADE"), index=True)
    trigger: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    location: Mapped[str | None] = mapped_column(String(40))
    collector_version: Mapped[str] = mapped_column(String(60))
    requested_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    requests_sent: Mapped[int] = mapped_column(Integer, default=0)
    counts: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    error: Mapped[str | None] = mapped_column(Text)


class Fetch(Base):
    """The once-per-day ledger: one row per (collector, hotel, task, stay, guests, currency, day)."""

    __tablename__ = "fetch_ledger"
    __table_args__ = (
        CheckConstraint("task IN ('calendar', 'offers')", name="task"),
        CheckConstraint(
            "outcome IN ('pending', 'ok', 'blocked', 'not_found', 'parse_error', 'failed')", name="outcome"
        ),
        Index("ix_fetch_ledger_hotel_task_date", "hotel_id", "task", "fetch_date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fetch_key: Mapped[str] = mapped_column(String(400), unique=True)
    collector: Mapped[str] = mapped_column(String(40))
    collector_version: Mapped[str] = mapped_column(String(60))
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"))
    task: Mapped[str] = mapped_column(String(20))
    check_in: Mapped[date] = mapped_column(Date)
    nights: Mapped[int] = mapped_column(Integer)
    adults: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    calendar_days: Mapped[int | None] = mapped_column(Integer)
    fetch_date: Mapped[date] = mapped_column(Date)
    run_id: Mapped[int | None] = mapped_column(ForeignKey("runs.id", ondelete="SET NULL"))
    outcome: Mapped[str] = mapped_column(String(20), default="pending")
    error: Mapped[str | None] = mapped_column(Text)
    attempts: Mapped[int] = mapped_column(Integer, default=1)
    requests: Mapped[int] = mapped_column(Integer, default=0)
    claimed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class RunFetch(Base):
    """Which fetches a run used, including ones it reused from earlier the same day."""

    __tablename__ = "run_fetches"

    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True)
    fetch_id: Mapped[int] = mapped_column(ForeignKey("fetch_ledger.id"), primary_key=True)
    reused: Mapped[bool] = mapped_column(Boolean, default=False)


class RateObservation(Base):
    """One price (or absence of one) as observed. Append-only."""

    __tablename__ = "rate_observations"
    __table_args__ = (
        Index("ix_rate_observations_hotel_task_check_in", "hotel_id", "source_task", "check_in"),
        Index("ix_rate_observations_fetch_id", "fetch_id"),
    )

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    hotel_id: Mapped[int] = mapped_column(ForeignKey("hotels.id"))
    fetch_id: Mapped[int] = mapped_column(ForeignKey("fetch_ledger.id"))
    source_task: Mapped[str] = mapped_column(String(20))
    check_in: Mapped[date] = mapped_column(Date)
    nights: Mapped[int] = mapped_column(Integer)
    adults: Mapped[int] = mapped_column(Integer)
    currency: Mapped[str] = mapped_column(String(3))
    available: Mapped[bool] = mapped_column(Boolean)
    day_status: Mapped[str | None] = mapped_column(String(20))
    channel: Mapped[str | None] = mapped_column(String(200))
    is_official_site: Mapped[bool | None] = mapped_column(Boolean)
    is_sponsored: Mapped[bool | None] = mapped_column(Boolean)
    room: Mapped[str | None] = mapped_column(String(500))
    rate_plan: Mapped[str | None] = mapped_column(String(120))
    free_cancellation: Mapped[bool | None] = mapped_column(Boolean)
    cancellation_deadline: Mapped[str | None] = mapped_column(String(60))
    breakfast: Mapped[bool | None] = mapped_column(Boolean)
    is_member_rate: Mapped[bool | None] = mapped_column(Boolean)
    price_per_night: Mapped[float | None] = mapped_column(Money)
    price_total: Mapped[float | None] = mapped_column(Money)
    total_before_taxes: Mapped[float | None] = mapped_column(Money)
    taxes: Mapped[float | None] = mapped_column(Money)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class RawResponse(Base):
    __tablename__ = "raw_responses"

    id: Mapped[int] = mapped_column(BigId, primary_key=True)
    fetch_id: Mapped[int | None] = mapped_column(ForeignKey("fetch_ledger.id"), index=True)
    collector_version: Mapped[str] = mapped_column(String(60))
    rpc_id: Mapped[str] = mapped_column(String(40))
    request: Mapped[str] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    problem: Mapped[str | None] = mapped_column(Text)
    body_gzip: Mapped[bytes | None] = mapped_column(LargeBinary)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

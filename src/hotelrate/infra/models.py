from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import Date, DateTime, Enum, ForeignKey, Integer, Numeric, String, Text, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from hotelrate.schemas import Platform


class Base(DeclarativeBase):
    pass


class HotelMaster(Base):
    __tablename__ = "hotel_master"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    city: Mapped[str] = mapped_column(String(120), index=True, nullable=False)
    country: Mapped[str] = mapped_column(String(120), nullable=False)
    latitude: Mapped[float | None] = mapped_column(nullable=True)
    longitude: Mapped[float | None] = mapped_column(nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    mappings: Mapped[list[HotelMapping]] = relationship(back_populates="hotel", cascade="all,delete")


class HotelMapping(Base):
    __tablename__ = "hotel_mapping"
    __table_args__ = (
        UniqueConstraint("platform", "platform_hotel_id", name="uq_platform_hotel_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    hotel_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hotel_master.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    platform: Mapped[Platform] = mapped_column(Enum(Platform, name="platform_enum"), index=True)
    platform_hotel_id: Mapped[str] = mapped_column(String(255), nullable=False)
    platform_hotel_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    platform_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    hotel: Mapped[HotelMaster] = relationship(back_populates="mappings")
    snapshots: Mapped[list[RateSnapshot]] = relationship(back_populates="mapping", cascade="all,delete")


class RateSnapshot(Base):
    __tablename__ = "rate_snapshot"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    mapping_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("hotel_mapping.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    platform: Mapped[Platform] = mapped_column(Enum(Platform, name="platform_enum"), index=True)
    room_name: Mapped[str] = mapped_column(String(255), nullable=False)
    meal_plan: Mapped[str | None] = mapped_column(String(255), nullable=True)
    cancellation_policy: Mapped[str | None] = mapped_column(Text, nullable=True)
    currency: Mapped[str] = mapped_column(String(3), nullable=False)
    base_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    tax_fee: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    final_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    checkin: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    checkout: Mapped[date] = mapped_column(Date, index=True, nullable=False)
    adults: Mapped[int] = mapped_column(Integer, nullable=False)
    children: Mapped[int] = mapped_column(Integer, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    scraped_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True, nullable=False)
    rate_hash: Mapped[str] = mapped_column(String(64), index=True, nullable=False)

    mapping: Mapped[HotelMapping] = relationship(back_populates="snapshots")


class CrawlJobLog(Base):
    __tablename__ = "crawl_job_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    platform: Mapped[Platform] = mapped_column(Enum(Platform, name="platform_enum"), index=True)
    target_count: Mapped[int] = mapped_column(Integer, nullable=False)
    success_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    duration_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)

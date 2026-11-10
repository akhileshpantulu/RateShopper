from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class Platform(str, Enum):
    TRIP = "trip"
    BOOKING = "booking"
    GOOGLE_HOTELS = "google_hotels"
    AGODA = "agoda"


class SearchRequest(BaseModel):
    platform_hotel_id: str
    checkin: date
    checkout: date
    adults: int = Field(default=2, ge=1, le=8)
    children: int = Field(default=0, ge=0, le=6)
    currency: str = Field(
        default="USD",
        min_length=3,
        max_length=3,
        description="ISO 4217; collectors pass uppercased codes to OTAs.",
    )
    locale: str = Field(
        default="en-US",
        min_length=2,
        max_length=35,
        description="BCP 47 locale for Playwright, Accept-Language, and site query/path hints.",
    )
    market: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z]{2}$",
        description="ISO 3166-1 alpha-2 country for Google Travel gl=; optional elsewhere.",
    )
    platform_url: str | None = None

    @field_validator("market", mode="before")
    @classmethod
    def _empty_market_to_none(cls, value: object) -> object:
        if value == "":
            return None
        return value


class RawRateOffer(BaseModel):
    platform: Platform
    platform_hotel_id: str
    room_name: str
    meal_plan: str | None = None
    cancellation_policy: str | None = None
    currency: str = Field(min_length=3, max_length=3)
    base_price: Decimal = Field(default=Decimal("0"))
    tax_fee: Decimal = Field(default=Decimal("0"))
    final_price: Decimal
    checkin: date
    checkout: date
    adults: int
    children: int
    source_url: str | None = None
    collected_at: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    raw_payload: dict[str, Any] = Field(default_factory=dict)


class NormalizedSnapshot(BaseModel):
    mapping_id: UUID
    platform: Platform
    room_name: str
    meal_plan: str | None = None
    cancellation_policy: str | None = None
    currency: str
    base_price: Decimal
    tax_fee: Decimal
    final_price: Decimal
    checkin: date
    checkout: date
    adults: int
    children: int
    source_url: str | None = None
    raw_payload: dict[str, Any] = Field(default_factory=dict)
    scraped_at: datetime
    rate_hash: str

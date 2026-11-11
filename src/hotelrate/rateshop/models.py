"""Results of the rate-shop collector. Prices are in the currency the call asked for."""

from __future__ import annotations

from datetime import date, datetime, timezone
from enum import Enum
from statistics import median

from pydantic import BaseModel, Field


def _now() -> datetime:
    return datetime.now(timezone.utc)


class HotelCandidate(BaseModel):
    """One search result, for a person to confirm as the hotel they meant."""

    source_id: str = Field(description="Google property token; pass it to the other calls.")
    name: str | None
    hotel_class: int | None = None
    property_type: str | None = None
    rating: float | None = None
    reviews_count: int | None = None
    latitude: float | None = None
    longitude: float | None = None
    rate_per_night: float | None = None
    currency: str | None = None
    google_url: str | None = None


class HotelLocation(BaseModel):
    """Where a hotel is and what Google shows about it, from its prices page."""

    source_id: str
    name: str | None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    hotel_class: int | None = None
    rating: float | None = None
    reviews_count: int | None = None
    phone: str | None = None
    website: str | None = None
    google_url: str | None = None


class DayStatus(str, Enum):
    PRICED = "priced"
    # Inside the horizon Google prices, but no price shown: a possible sell-out.
    NO_PRICE = "no_price"
    # Beyond the last date Google prices: not offered, never a sell-out signal.
    NOT_OFFERED = "not_offered"


class CalendarDay(BaseModel):
    check_in: date
    check_out: date
    status: DayStatus
    rate_per_night: float | None = None
    total: float | None = None
    total_before_taxes: float | None = None
    taxes_and_fees: float | None = None
    currency: str | None = None
    cheapest: bool = False


class CalendarResult(BaseModel):
    hotel_id: str
    matched_by: str = Field(description="How the hotel was identified: token, link, place or name.")
    start: date
    days_requested: int
    nights: int
    adults: int
    currency: str
    horizon: date | None = Field(description="Last check-in date Google priced.")
    priced_days: int
    no_price_days: int
    not_offered_days: int
    lowest_total: float | None = None
    median_total: float | None = None
    highest_total: float | None = None
    days: list[CalendarDay]
    collector_version: str
    fetched_at: datetime = Field(default_factory=_now)

    @classmethod
    def build(
        cls,
        *,
        hotel_id: str,
        matched_by: str,
        start: date,
        days: list[CalendarDay],
        nights: int,
        adults: int,
        currency: str,
        collector_version: str,
    ) -> CalendarResult:
        totals = [day.total for day in days if day.total is not None]
        priced = [day for day in days if day.status is DayStatus.PRICED]
        return cls(
            hotel_id=hotel_id,
            matched_by=matched_by,
            start=start,
            days_requested=len(days),
            nights=nights,
            adults=adults,
            currency=currency,
            horizon=max((day.check_in for day in priced), default=None),
            priced_days=len(priced),
            no_price_days=sum(day.status is DayStatus.NO_PRICE for day in days),
            not_offered_days=sum(day.status is DayStatus.NOT_OFFERED for day in days),
            lowest_total=min(totals) if totals else None,
            median_total=round(median(totals), 2) if totals else None,
            highest_total=max(totals) if totals else None,
            days=days,
            collector_version=collector_version,
        )


class OfferRow(BaseModel):
    """One price shown by one booking channel; one row per rate when Google lists rooms."""

    channel: str
    is_official_site: bool
    is_sponsored: bool
    room: str | None = None
    rate_plan: str | None = Field(
        default=None, description="Derived from the flags Google shows; None when no room-level rate came back."
    )
    free_cancellation: bool | None = None
    cancellation_deadline: str | None = None
    breakfast: bool | None = None
    price_per_night: float | None = None
    price_total: float | None = None
    currency: str
    is_member_rate: bool = False
    non_member_price_per_night: float | None = None
    partner_url: str | None = None


class OffersResult(BaseModel):
    hotel_id: str
    hotel_name: str | None
    matched_by: str
    check_in: date
    check_out: date
    nights: int
    adults: int
    currency: str
    lowest_price_per_night: float | None
    channels: int
    offers: list[OfferRow]
    google_url: str | None = None
    address: str | None = None
    latitude: float | None = None
    longitude: float | None = None
    collector_version: str
    fetched_at: datetime = Field(default_factory=_now)


class DateOutcome(str, Enum):
    OK = "ok"
    BLOCKED = "blocked"
    NOT_FOUND = "not_found"
    PARSE_ERROR = "parse_error"
    FAILED = "failed"


class DateOffers(BaseModel):
    check_in: date
    outcome: DateOutcome
    error: str | None = None
    result: OffersResult | None = None


class RateShopResult(BaseModel):
    hotel_id: str
    hotel_name: str | None = None
    calendar: CalendarResult
    offers: list[DateOffers] = Field(default_factory=list)
    offer_dates_ok: int = 0
    offer_dates_failed: int = 0
    elapsed_seconds: float
    requests_sent: int

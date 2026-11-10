"""Rate shopping through Google Hotels' data endpoints: price calendars and every channel's offers, no browser."""

from hotelrate.rateshop.errors import Blocked, CollectorError, NotFound, ParseError, SourceError
from hotelrate.rateshop.google_hotels import COLLECTOR_VERSION, GoogleHotelsCollector, RawResponse
from hotelrate.rateshop.models import (
    CalendarDay,
    CalendarResult,
    DateOffers,
    DateOutcome,
    DayStatus,
    HotelCandidate,
    OfferRow,
    OffersResult,
    RateShopResult,
)
from hotelrate.rateshop.policy import PolicyConfig, RequestPolicy
from hotelrate.rateshop.sampling import sample_dates
from hotelrate.rateshop.shop import rate_shop

__all__ = [
    "COLLECTOR_VERSION",
    "Blocked",
    "CalendarDay",
    "CalendarResult",
    "CollectorError",
    "DateOffers",
    "DateOutcome",
    "DayStatus",
    "GoogleHotelsCollector",
    "HotelCandidate",
    "NotFound",
    "OfferRow",
    "OffersResult",
    "ParseError",
    "PolicyConfig",
    "RateShopResult",
    "RawResponse",
    "RequestPolicy",
    "SourceError",
    "rate_shop",
    "sample_dates",
]

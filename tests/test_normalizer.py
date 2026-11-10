"""Tests for core/normalizer.py — normalize_offer and rate hash."""

from __future__ import annotations

import uuid
from datetime import date
from decimal import Decimal

from hotelrate.core.normalizer import normalize_offer, _build_rate_hash
from hotelrate.schemas import Platform, RawRateOffer


def _make_raw_offer(**overrides) -> RawRateOffer:
    defaults = dict(
        platform=Platform.TRIP,
        platform_hotel_id="12345",
        room_name="Deluxe Room",
        meal_plan="Breakfast included",
        cancellation_policy="Free cancellation before 24h",
        currency="USD",
        base_price=Decimal("100.00"),
        tax_fee=Decimal("12.00"),
        final_price=Decimal("112.00"),
        checkin=date(2026, 5, 1),
        checkout=date(2026, 5, 2),
        adults=2,
        children=0,
        source_url="https://trip.com/hotels/detail/?hotelId=12345",
        raw_payload={"mode": "trip_room_list_oversea"},
    )
    defaults.update(overrides)
    return RawRateOffer(**defaults)


def test_normalize_offer_basic():
    mapping_id = uuid.uuid4()
    raw = _make_raw_offer()
    snapshot = normalize_offer(mapping_id, raw)

    assert snapshot.mapping_id == mapping_id
    assert snapshot.platform == Platform.TRIP
    assert snapshot.room_name == "Deluxe Room"
    assert snapshot.currency == "USD"
    assert snapshot.base_price == Decimal("100.00")
    assert snapshot.tax_fee == Decimal("12.00")
    assert snapshot.final_price == Decimal("112.00")
    assert snapshot.checkin == date(2026, 5, 1)
    assert snapshot.checkout == date(2026, 5, 2)
    assert snapshot.adults == 2
    assert snapshot.children == 0
    assert snapshot.rate_hash  # non-empty


def test_normalize_offer_zero_final_price_recalculated():
    """When final_price is 0, it should be recalculated from base + tax."""
    mapping_id = uuid.uuid4()
    raw = _make_raw_offer(
        base_price=Decimal("80.00"),
        tax_fee=Decimal("10.00"),
        final_price=Decimal("0"),
    )
    snapshot = normalize_offer(mapping_id, raw)
    assert snapshot.final_price == Decimal("90.00")


def test_normalize_offer_currency_uppercased():
    mapping_id = uuid.uuid4()
    raw = _make_raw_offer(currency="usd")
    snapshot = normalize_offer(mapping_id, raw)
    assert snapshot.currency == "USD"


def test_rate_hash_deterministic():
    raw = _make_raw_offer()
    hash1 = _build_rate_hash(raw)
    hash2 = _build_rate_hash(raw)
    assert hash1 == hash2
    assert len(hash1) == 40  # SHA-1 hex digest


def test_rate_hash_differs_for_different_price():
    raw1 = _make_raw_offer(final_price=Decimal("100.00"))
    raw2 = _make_raw_offer(final_price=Decimal("200.00"))
    assert _build_rate_hash(raw1) != _build_rate_hash(raw2)


def test_rate_hash_differs_for_different_platform():
    raw1 = _make_raw_offer(platform=Platform.TRIP)
    raw2 = _make_raw_offer(platform=Platform.BOOKING)
    assert _build_rate_hash(raw1) != _build_rate_hash(raw2)

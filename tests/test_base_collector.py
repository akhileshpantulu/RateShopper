"""Tests for collectors/base.py — price extraction, payload parsing, URL helpers."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from hotelrate.collectors.base import BaseCollector
from hotelrate.config import get_settings
from hotelrate.schemas import Platform, RawRateOffer, SearchRequest


class _StubCollector(BaseCollector):
    """Minimal concrete subclass for testing base methods."""

    platform = Platform.TRIP

    async def fetch_rates(self, request: SearchRequest) -> list[RawRateOffer]:
        return []


def _make_collector() -> _StubCollector:
    settings = get_settings().model_copy(update={"demo_mode": True})
    return _StubCollector(settings)


def _make_request(**overrides) -> SearchRequest:
    defaults = dict(
        platform_hotel_id="12345",
        checkin=date(2026, 5, 1),
        checkout=date(2026, 5, 2),
        adults=2,
        children=0,
        currency="USD",
    )
    defaults.update(overrides)
    return SearchRequest(**defaults)


# ---------- _to_decimal ----------

class TestToDecimal:
    def test_int(self):
        c = _make_collector()
        assert c._to_decimal(123) == Decimal("123")

    def test_float(self):
        c = _make_collector()
        assert c._to_decimal(99.5) == Decimal("99.5")

    def test_string(self):
        c = _make_collector()
        assert c._to_decimal("250.00") == Decimal("250.00")

    def test_string_with_comma(self):
        c = _make_collector()
        assert c._to_decimal("1,234.56") == Decimal("1234.56")

    def test_european_comma(self):
        c = _make_collector()
        # European format: comma as decimal separator, no dot
        assert c._to_decimal("1234,56") == Decimal("1234.56")

    def test_none(self):
        c = _make_collector()
        assert c._to_decimal(None) is None

    def test_bool_rejected(self):
        c = _make_collector()
        assert c._to_decimal(True) is None
        assert c._to_decimal(False) is None

    def test_empty_string(self):
        c = _make_collector()
        assert c._to_decimal("") is None

    def test_currency_prefix(self):
        c = _make_collector()
        assert c._to_decimal("$150.00") == Decimal("150.00")


# ---------- merge_url_query ----------

class TestMergeUrlQuery:
    def test_basic(self):
        c = _make_collector()
        result = c.merge_url_query("https://example.com/path", {"a": "1", "b": "2"})
        assert "a=1" in result
        assert "b=2" in result

    def test_overwrite_existing(self):
        c = _make_collector()
        result = c.merge_url_query("https://example.com/?a=old", {"a": "new"})
        assert "a=new" in result
        assert "a=old" not in result

    def test_none_value_skipped(self):
        c = _make_collector()
        result = c.merge_url_query("https://example.com/", {"a": "1", "b": None})
        assert "a=1" in result
        assert "b=" not in result


# ---------- remove_url_query_keys ----------

class TestRemoveUrlQueryKeys:
    def test_basic(self):
        c = _make_collector()
        result = c.remove_url_query_keys(
            "https://example.com/?a=1&b=2&c=3",
            ["b"],
        )
        assert "a=1" in result
        assert "c=3" in result
        assert "b=" not in result

    def test_case_insensitive(self):
        c = _make_collector()
        result = c.remove_url_query_keys(
            "https://example.com/?SID=abc&name=test",
            ["sid"],
        )
        assert "name=test" in result
        assert "SID" not in result


# ---------- parse_payloads_to_offers ----------

class TestParsePayloadsToOffers:
    def test_basic_parsing(self):
        c = _make_collector()
        request = _make_request()
        payloads = [
            {
                "url": "https://api.example.com/rooms",
                "status": 200,
                "data": {
                    "rooms": [
                        {
                            "roomName": "Deluxe King",
                            "price": 150,
                            "currency": "USD",
                        },
                        {
                            "roomName": "Standard Twin",
                            "price": 100,
                            "currency": "USD",
                        },
                    ]
                },
            }
        ]
        offers = c.parse_payloads_to_offers(request=request, payloads=payloads)
        assert len(offers) == 2
        names = {o.room_name for o in offers}
        assert "Deluxe King" in names
        assert "Standard Twin" in names

    def test_deduplication(self):
        c = _make_collector()
        request = _make_request()
        payloads = [
            {
                "url": "https://api.example.com/rooms",
                "status": 200,
                "data": {
                    "rooms": [
                        {"roomName": "Deluxe", "price": 150, "currency": "USD"},
                        {"roomName": "Deluxe", "price": 150, "currency": "USD"},
                    ]
                },
            }
        ]
        offers = c.parse_payloads_to_offers(request=request, payloads=payloads)
        assert len(offers) == 1

    def test_skip_low_price(self):
        c = _make_collector()
        request = _make_request()
        payloads = [
            {
                "url": "https://api.example.com",
                "status": 200,
                "data": {"roomName": "Cheap", "price": 5, "currency": "USD"},
            }
        ]
        offers = c.parse_payloads_to_offers(request=request, payloads=payloads)
        assert len(offers) == 0  # price < 20 is rejected

    def test_max_offers_limit(self):
        c = _make_collector()
        request = _make_request()
        rooms = [
            {"roomName": f"Room {i}", "price": 100 + i, "currency": "USD"}
            for i in range(30)
        ]
        payloads = [{"url": "https://api.example.com", "status": 200, "data": {"list": rooms}}]
        offers = c.parse_payloads_to_offers(request=request, payloads=payloads, max_offers=5)
        assert len(offers) <= 5


# ---------- count_structured_offers ----------

class TestCountStructuredOffers:
    def test_counts_only_structured(self):
        c = _make_collector()
        offers = [
            RawRateOffer(
                platform=Platform.TRIP,
                platform_hotel_id="1",
                room_name="Room A",
                currency="USD",
                final_price=Decimal("100"),
                checkin=date(2026, 5, 1),
                checkout=date(2026, 5, 2),
                adults=2,
                children=0,
                raw_payload={"mode": "trip_room_list_oversea"},
            ),
            RawRateOffer(
                platform=Platform.TRIP,
                platform_hotel_id="1",
                room_name="Room B",
                currency="USD",
                final_price=Decimal("200"),
                checkin=date(2026, 5, 1),
                checkout=date(2026, 5, 2),
                adults=2,
                children=0,
                raw_payload={"mode": "text_fallback"},
            ),
            RawRateOffer(
                platform=Platform.TRIP,
                platform_hotel_id="1",
                room_name="Room C",
                currency="USD",
                final_price=Decimal("300"),
                checkin=date(2026, 5, 1),
                checkout=date(2026, 5, 2),
                adults=2,
                children=0,
                raw_payload={"mode": "demo"},
            ),
        ]
        assert c.count_structured_offers(offers) == 1


# ---------- build_demo_offer ----------

class TestBuildDemoOffer:
    def test_demo_offer_fields(self):
        c = _make_collector()
        request = _make_request()
        offer = c.build_demo_offer(request, low=80, high=260)
        assert offer.platform == Platform.TRIP
        assert offer.room_name == "Standard Room"
        assert offer.raw_payload == {"mode": "demo"}
        assert offer.base_price > 0
        assert offer.final_price > offer.base_price  # includes tax

    def test_demo_offer_deterministic(self):
        c = _make_collector()
        request = _make_request()
        offer1 = c.build_demo_offer(request, low=80, high=260)
        offer2 = c.build_demo_offer(request, low=80, high=260)
        assert offer1.final_price == offer2.final_price


# ---------- _extract_prices_from_text ----------

class TestExtractPricesFromText:
    def test_basic_usd(self):
        c = _make_collector()
        text = "The room rate is $150 per night including taxes."
        prices = c._extract_prices_from_text(
            text=text,
            currency="USD",
            min_price=Decimal("20"),
            max_price=Decimal("10000"),
            max_results=5,
        )
        assert Decimal("150.00") in prices

    def test_multiple_currencies(self):
        c = _make_collector()
        text = "Price: ¥800 total for tonight"
        prices = c._extract_prices_from_text(
            text=text,
            currency="CNY",
            min_price=Decimal("20"),
            max_price=Decimal("10000"),
            max_results=5,
        )
        assert Decimal("800.00") in prices

    def test_filters_below_min(self):
        c = _make_collector()
        text = "$5 fee and $150 nightly rate"
        prices = c._extract_prices_from_text(
            text=text,
            currency="USD",
            min_price=Decimal("20"),
            max_price=Decimal("10000"),
            max_results=5,
        )
        assert Decimal("5.00") not in prices
        assert Decimal("150.00") in prices

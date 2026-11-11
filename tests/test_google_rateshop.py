"""Google Hotels rate-shop collector, against recorded Google answers (tests/fixtures/google_hotels/)."""

from __future__ import annotations

import asyncio
import gzip
import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs

import httpx
import pytest

from hotelrate.config import Settings
from hotelrate.core.browser_policy import HONEST_HTTP_USER_AGENT
from hotelrate.rateshop import (
    Blocked,
    DateOutcome,
    DayStatus,
    GoogleHotelsCollector,
    ParseError,
    PolicyConfig,
    RequestPolicy,
    SourceError,
    rate_shop,
    sample_dates,
)
from hotelrate.rateshop.google_hotels import derive_rate_plan
from hotelrate.rateshop.sampling import second_monday

FIXTURES = Path(__file__).parent / "fixtures" / "google_hotels"
TODAY = date(2026, 11, 10)
START = date(2026, 12, 10)
SAVOY = "ChUIqbjvp9r_u_oVGgkvbS8wMjIyMGQQAQ"
STRAND_PALACE = "ChUI24LFxcHr4N5sGgkvbS8wYzNnaGwQAQ"
FIXTURE_BY_RPC = {
    "AtySUc": "search_the_savoy_london",
    "yY52ce": "calendar_the_savoy_365d",
    "lvkpBc": "prices_strand_palace",
}


def _fixture(name: str) -> dict[str, Any]:
    with gzip.open(FIXTURES / f"{name}.json.gz", "rt", encoding="utf-8") as handle:
        return json.load(handle)


def _rpc_id(request: httpx.Request) -> str:
    return request.url.params["rpcids"]


def _call_request(request: httpx.Request) -> str:
    """The call's own request array, as the JSON text Google receives."""
    form = parse_qs(request.content.decode())
    return json.loads(form["f.req"][0])[0][0][1]


def _replay(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text=_fixture(FIXTURE_BY_RPC[_rpc_id(request)])["body"])


class Recorder:
    """An httpx handler that records each request and answers with `answer` (default: the recorded fixture)."""

    def __init__(self, answer=_replay) -> None:
        self.answer = answer
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.answer(request)

    @property
    def rpc_ids(self) -> list[str]:
        return [_rpc_id(request) for request in self.requests]


def _settings(**overrides: Any) -> Settings:
    values = {"google_hotels_country": "gb", "default_currency": "GBP", **overrides}
    return Settings(_env_file=None, **values)


def _fast_policy(**overrides: Any) -> RequestPolicy:
    values = {
        "max_in_flight": 2,
        "pause_min_seconds": 0,
        "pause_max_seconds": 0,
        "max_retries": 3,
        "retry_base_seconds": 0,
        "block_pause_seconds": 0,
        **overrides,
    }
    return RequestPolicy(PolicyConfig(**values))


def _collector(handler, *, policy: RequestPolicy | None = None, **settings: Any) -> GoogleHotelsCollector:
    return GoogleHotelsCollector(
        _settings(**settings),
        policy=policy or _fast_policy(),
        http_transport=httpx.MockTransport(handler),
        today=lambda: TODAY,
    )


# -- parsing recorded answers ------------------------------------------------------------------------------------------


async def test_resolve_hotel_lists_candidates() -> None:
    async with _collector(Recorder()) as collector:
        candidates = await collector.resolve_hotel("The Savoy", "London")

    assert 1 <= len(candidates) <= 5
    assert candidates[0].name == "The Savoy"
    assert candidates[0].source_id == SAVOY
    assert candidates[0].hotel_class == 5
    assert candidates[0].rate_per_night is not None


async def test_price_calendar_marks_no_price_and_not_offered_days() -> None:
    recorder = Recorder()
    async with _collector(recorder) as collector:
        calendar = await collector.price_calendar(SAVOY, start=START, days=365)

    assert recorder.rpc_ids == ["yY52ce"]
    assert calendar.hotel_id == SAVOY
    assert calendar.matched_by == "token"
    assert len(calendar.days) == calendar.days_requested == 365
    assert calendar.days[0].check_in == START
    assert calendar.days[-1].check_in == START + timedelta(days=364)
    assert calendar.horizon == date(2027, 10, 14)
    in_horizon = (calendar.horizon - START).days + 1
    assert calendar.priced_days == 300
    assert calendar.no_price_days == in_horizon - 300
    assert calendar.not_offered_days == 365 - in_horizon
    statuses = {day.status for day in calendar.days if day.check_in > calendar.horizon}
    assert statuses == {DayStatus.NOT_OFFERED}
    priced = [day for day in calendar.days if day.status is DayStatus.PRICED]
    assert all(day.total is not None and day.currency == "GBP" for day in priced)
    assert calendar.lowest_total <= calendar.median_total <= calendar.highest_total
    assert any(day.cheapest for day in priced)


async def test_price_calendar_starts_today_by_default() -> None:
    recorder = Recorder()
    async with _collector(recorder) as collector:
        calendar = await collector.price_calendar(SAVOY, days=30)

    assert calendar.start == TODAY
    assert f"[[{TODAY.year},{TODAY.month},{TODAY.day}]," in _call_request(recorder.requests[0])


async def test_price_calendar_by_name_resolves_through_search() -> None:
    recorder = Recorder()
    async with _collector(recorder) as collector:
        calendar = await collector.price_calendar("The Savoy London", start=START, days=365)

    assert recorder.rpc_ids == ["AtySUc", "yY52ce"]
    assert calendar.hotel_id == SAVOY
    assert calendar.matched_by in {"place", "name"}


async def test_channel_offers_lists_every_channel() -> None:
    async with _collector(Recorder()) as collector:
        offers = await collector.channel_offers(STRAND_PALACE, check_in=START)

    channels = {row.channel for row in offers.offers}
    assert offers.hotel_id == STRAND_PALACE
    assert offers.check_out == START + timedelta(days=1)
    assert offers.channels >= 20
    assert {"Booking.com", "Agoda", "Trip.com", "Hotels.com"} <= channels
    assert any(row.is_sponsored for row in offers.offers)
    assert all(row.price_per_night is not None and row.currency == "GBP" for row in offers.offers)
    assert offers.lowest_price_per_night == min(row.price_per_night for row in offers.offers)
    # This answer carried no room-level rates, so no rate plan is invented.
    assert all(row.room is None and row.rate_plan is None for row in offers.offers)


async def test_offers_and_locate_carry_coordinates() -> None:
    recorder = Recorder()
    async with _collector(recorder) as collector:
        offers = await collector.channel_offers(STRAND_PALACE, check_in=START)
        place = await collector.locate(STRAND_PALACE)

    assert (offers.latitude, offers.longitude) == (51.511101, -0.1208727)
    assert offers.address == "372 Strand, London WC2R 0JJ"
    assert place.source_id == STRAND_PALACE and place.name == "Strand Palace"
    assert (place.latitude, place.longitude, place.address) == (offers.latitude, offers.longitude, offers.address)
    assert place.hotel_class == 4 and recorder.rpc_ids == ["lvkpBc", "lvkpBc"]


def test_distance() -> None:
    from hotelrate.core.geo import distance_km

    # Strand Palace to The Savoy is about 150 m along the Strand.
    assert distance_km(51.511101, -0.1208727, 51.5101, -0.1203) == pytest.approx(0.12, abs=0.02)
    assert distance_km(51.5, -0.12, 51.5, -0.12) == 0


def test_rate_plan_is_derived_from_flags() -> None:
    assert derive_rate_plan(True, True) == "Free cancellation, breakfast"
    assert derive_rate_plan(False, False) == "Standard cancellation, room only"


# -- request policy and failure handling -------------------------------------------------------------------------------


async def test_requests_use_honest_user_agent_by_default() -> None:
    recorder = Recorder()
    async with _collector(recorder) as collector:
        await collector.price_calendar(SAVOY, start=START, days=30)

    assert recorder.requests[0].headers["User-Agent"] == HONEST_HTTP_USER_AGENT
    assert recorder.requests[0].url.params["gl"] == "gb"


async def test_block_is_retried_and_pauses_the_process() -> None:
    answers = iter([httpx.Response(429, text="slow down")])

    def first_blocked(request: httpx.Request) -> httpx.Response:
        return next(answers, None) or _replay(request)

    recorder = Recorder(first_blocked)
    policy = _fast_policy()
    async with _collector(recorder, policy=policy) as collector:
        calendar = await collector.price_calendar(SAVOY, start=START, days=365)

    assert calendar.priced_days == 300
    assert len(recorder.requests) == 2
    assert policy.blocks_seen == 1


async def test_block_on_every_attempt_stops_after_three_retries() -> None:
    recorder = Recorder(lambda request: httpx.Response(429))
    async with _collector(recorder) as collector:
        with pytest.raises(Blocked):
            await collector.price_calendar(SAVOY, start=START, days=30)

    assert len(recorder.requests) == 4


async def test_unusual_traffic_page_counts_as_block() -> None:
    def sorry(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/sorry/"):
            return httpx.Response(200, text="<html>unusual traffic</html>")
        return httpx.Response(302, headers={"Location": "https://www.google.com/sorry/index"})

    policy = _fast_policy(max_retries=1)
    async with _collector(Recorder(sorry), policy=policy) as collector:
        with pytest.raises(Blocked):
            await collector.price_calendar(SAVOY, start=START, days=30)

    assert policy.blocks_seen == 2


async def test_unexpected_status_is_not_retried() -> None:
    recorder = Recorder(lambda request: httpx.Response(400, text="bad request"))
    async with _collector(recorder) as collector:
        with pytest.raises(SourceError):
            await collector.price_calendar(SAVOY, start=START, days=30)

    assert len(recorder.requests) == 1


async def test_unreadable_answer_keeps_the_raw_body() -> None:
    recorder = Recorder(lambda request: httpx.Response(200, text="not a batchexecute answer"))
    async with _collector(recorder) as collector:
        with pytest.raises(ParseError) as caught:
            await collector.price_calendar(SAVOY, start=START, days=30)

    assert caught.value.raw == "not a batchexecute answer"
    assert len(recorder.requests) == 4


async def test_no_more_than_max_in_flight_requests() -> None:
    in_flight = peak = 0

    async def slow(request: httpx.Request) -> httpx.Response:
        nonlocal in_flight, peak
        in_flight += 1
        peak = max(peak, in_flight)
        await asyncio.sleep(0.02)
        in_flight -= 1
        return _replay(request)

    async with _collector(slow, policy=_fast_policy(max_in_flight=2)) as collector:
        await asyncio.gather(
            *(collector.channel_offers(STRAND_PALACE, check_in=START + timedelta(days=n)) for n in range(6))
        )

    assert peak == 2


async def test_rate_shop_gives_each_offers_date_its_own_outcome() -> None:
    blocked_day = START + timedelta(days=2)

    def block_one_date(request: httpx.Request) -> httpx.Response:
        # The stay's dates go out as [[check-in],[check-out],nights,...]; match the check-in only.
        check_in = f"[[{blocked_day.year},{blocked_day.month},{blocked_day.day}],"
        if _rpc_id(request) == "lvkpBc" and check_in in _call_request(request):
            return httpx.Response(429)
        return _replay(request)

    recorder = Recorder(block_one_date)
    async with _collector(recorder, policy=_fast_policy(max_retries=0)) as collector:
        result = await rate_shop(
            collector, SAVOY, start=START, days=365, offer_dates=[START, START + timedelta(days=1), blocked_day]
        )

    assert result.calendar.priced_days == 300
    assert result.requests_sent == 4
    assert result.offer_dates_ok == 2
    assert result.offer_dates_failed == 1
    outcomes = {item.check_in: item.outcome for item in result.offers}
    assert outcomes[blocked_day] is DateOutcome.BLOCKED
    # Offers calls reuse the calendar's token and never search again.
    assert "AtySUc" not in recorder.rpc_ids


# -- policy timing, with a fake clock ----------------------------------------------------------------------------------


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def __call__(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(round(seconds, 3))
        self.now += seconds


async def test_pause_between_request_starts() -> None:
    clock = FakeClock()
    config = PolicyConfig(max_in_flight=1, pause_min_seconds=5, pause_max_seconds=5)
    policy = RequestPolicy(config, sleep=clock.sleep, clock=clock)
    for _ in range(3):
        async with policy.slot():
            pass

    assert clock.slept == [5, 5]


async def test_block_pauses_the_next_request() -> None:
    clock = FakeClock()
    config = PolicyConfig(pause_min_seconds=0, pause_max_seconds=0, block_pause_seconds=300)
    policy = RequestPolicy(config, sleep=clock.sleep, clock=clock)
    async with policy.slot():
        policy.note_block()
    async with policy.slot():
        pass

    assert clock.slept == [300]


def test_retry_delays_grow() -> None:
    policy = RequestPolicy(PolicyConfig(retry_base_seconds=30))
    assert 24 <= policy.retry_delay(1) <= 36
    assert 72 <= policy.retry_delay(2) <= 108
    assert 216 <= policy.retry_delay(3) <= 324


def test_pause_range_is_validated() -> None:
    with pytest.raises(ValueError):
        _settings(rateshop_pause_min_seconds=8, rateshop_pause_max_seconds=3)


# -- sample dates ------------------------------------------------------------------------------------------------------


def test_second_monday() -> None:
    assert second_monday(2026, 12) == date(2026, 12, 14)
    assert second_monday(2027, 2) == date(2027, 2, 8)


def test_sample_dates_one_week_a_month_plus_extras() -> None:
    event = date(2027, 1, 1)
    picked = sample_dates(date(2026, 12, 1), months=2, extra=[event])

    december = [date(2026, 12, 14) + timedelta(days=n) for n in range(7)]
    january = [date(2027, 1, 11) + timedelta(days=n) for n in range(7)]
    assert picked == sorted([*december, event, *january])
    assert all(day.weekday() == offset for offset, day in enumerate(december))


def test_sample_dates_drop_days_outside_the_window() -> None:
    picked = sample_dates(date(2026, 12, 16), months=1, horizon=date(2026, 12, 18))
    assert picked == [date(2026, 12, 16), date(2026, 12, 17), date(2026, 12, 18)]

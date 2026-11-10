from __future__ import annotations

import asyncio
from datetime import date

import pytest

from hotelrate.collectors.google_hotels.collector import GoogleHotelsCollector
from hotelrate.config import Settings
from hotelrate.schemas import (
    BatchQuoteRequest,
    LivePlatformResult,
    LiveQuoteRequest,
    LiveQuoteResponse,
    Platform,
)
from hotelrate.services.live_quote_service import LiveQuoteService


def test_build_probe_offsets_uses_full_window_for_booking() -> None:
    offsets = LiveQuoteService._build_probe_offsets(
        platform=Platform.BOOKING,
        probe_days_ahead=18,
        probe_step_days=6,
    )

    assert offsets == [0, 6, 12, 18]


def test_build_probe_offsets_covers_tail_when_window_not_divisible_by_step() -> None:
    offsets = LiveQuoteService._build_probe_offsets(
        platform=Platform.AGODA,
        probe_days_ahead=10,
        probe_step_days=3,
    )

    assert offsets == [0, 3, 6, 9, 10]


def test_build_probe_offsets_keeps_other_platforms_single_day() -> None:
    offsets = LiveQuoteService._build_probe_offsets(
        platform=Platform.TRIP,
        probe_days_ahead=18,
        probe_step_days=6,
    )

    assert offsets == [0]


def test_build_attempt_plan_interleaves_urls_by_offset() -> None:
    plan = LiveQuoteService._build_attempt_plan(
        urls=["u1", "u2"],
        offsets=[0, 6, 12, 18],
        max_attempts=5,
    )

    assert plan == [
        ("u1", 0),
        ("u2", 0),
        ("u1", 6),
        ("u2", 6),
        ("u1", 12),
    ]


def test_google_navigation_timeout_uses_google_override() -> None:
    collector = GoogleHotelsCollector(
        Settings(
            _env_file=None,
            request_timeout_seconds=25,
            google_request_timeout_seconds=90,
        )
    )

    assert collector._navigation_timeout_ms() == 90_000


@pytest.mark.asyncio
async def test_quote_runs_google_after_other_platforms(monkeypatch) -> None:
    service = LiveQuoteService(Settings(_env_file=None, demo_mode=True, platform_max_concurrency=3))
    request = LiveQuoteRequest(
        hotel_name="Atour Hotel Shanghai HuangPu Xintiandi Metro Station",
        city="Shanghai",
        checkin=date(2026, 4, 10),
        checkout=date(2026, 4, 11),
        platforms=[Platform.BOOKING, Platform.GOOGLE_HOTELS, Platform.TRIP],
    )
    events: list[tuple[str, Platform]] = []

    async def fake_quote_one_platform(
        *,
        platform: Platform,
        request: LiveQuoteRequest,
        on_progress=None,
    ) -> LivePlatformResult:
        events.append(("start", platform))
        if platform == Platform.GOOGLE_HOTELS:
            assert ("done", Platform.BOOKING) in events
            assert ("done", Platform.TRIP) in events
        await asyncio.sleep(0.01 if platform != Platform.GOOGLE_HOTELS else 0)
        events.append(("done", platform))
        return LivePlatformResult(platform=platform, diagnosis="ok")

    monkeypatch.setattr(service, "_quote_one_platform", fake_quote_one_platform)

    response = await service.quote(request)

    assert [result.platform for result in response.results] == request.platforms


@pytest.mark.asyncio
async def test_batch_quote_runs_google_requests_after_other_requests(monkeypatch) -> None:
    service = LiveQuoteService(
        Settings(
            _env_file=None,
            demo_mode=True,
            batch_hotel_concurrency=3,
            batch_task_concurrency=6,
        )
    )
    google_request = LiveQuoteRequest(
        hotel_name="Atour Hotel Shanghai HuangPu Xintiandi Metro Station",
        city="Shanghai",
        checkin=date(2026, 4, 10),
        checkout=date(2026, 4, 11),
        platforms=[Platform.GOOGLE_HOTELS],
    )
    trip_request = LiveQuoteRequest(
        hotel_name="Atour Hotel Shanghai HuangPu Xintiandi Metro Station",
        city="Shanghai",
        checkin=date(2026, 4, 10),
        checkout=date(2026, 4, 11),
        platforms=[Platform.TRIP],
    )
    booking_request = LiveQuoteRequest(
        hotel_name="Atour Hotel Shanghai HuangPu Xintiandi Metro Station",
        city="Shanghai",
        checkin=date(2026, 5, 20),
        checkout=date(2026, 5, 21),
        platforms=[Platform.BOOKING],
    )
    completed_regular: set[Platform] = set()

    async def fake_quote(req: LiveQuoteRequest, on_progress=None) -> LiveQuoteResponse:
        platform = req.platforms[0]
        if platform == Platform.GOOGLE_HOTELS:
            assert completed_regular == {Platform.TRIP, Platform.BOOKING}
        await asyncio.sleep(0.01 if platform != Platform.GOOGLE_HOTELS else 0)
        if platform != Platform.GOOGLE_HOTELS:
            completed_regular.add(platform)
        return LiveQuoteResponse(request=req, results=[])

    monkeypatch.setattr(service, "quote", fake_quote)

    response = await service.batch_quote(
        BatchQuoteRequest(
            hotels=[google_request, trip_request, booking_request],
            concurrency=3,
        )
    )

    assert response.successful == 3
    assert [item.request.platforms[0] for item in response.results] == [
        Platform.GOOGLE_HOTELS,
        Platform.TRIP,
        Platform.BOOKING,
    ]

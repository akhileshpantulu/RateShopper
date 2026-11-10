"""A full rate shop for one hotel: its price calendar, then channel offers on chosen check-in dates.

Each offers date has its own outcome, so a block or failure on one date never stops the others.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable
from datetime import date

from hotelrate.rateshop.errors import Blocked, NotFound, ParseError
from hotelrate.rateshop.google_hotels import GoogleHotelsCollector
from hotelrate.rateshop.models import DateOffers, DateOutcome, RateShopResult

Progress = Callable[[int, int, str], Awaitable[None]]


async def rate_shop(
    collector: GoogleHotelsCollector,
    hotel: str,
    *,
    start: date | None = None,
    days: int = 365,
    nights: int = 1,
    adults: int = 2,
    currency: str | None = None,
    offer_dates: Iterable[date] = (),
    on_progress: Progress | None = None,
) -> RateShopResult:
    started = time.monotonic()
    requests_before = collector.requests_sent
    dates = sorted(set(offer_dates))
    total_steps = 1 + len(dates)

    calendar = await collector.price_calendar(
        hotel, start=start, days=days, nights=nights, adults=adults, currency=currency
    )
    done = 1
    if on_progress:
        await on_progress(done, total_steps, f"calendar: {calendar.priced_days} priced days")

    # The calendar resolved the hotel, so offers calls use its token and never search again.
    token = calendar.hotel_id
    lock = asyncio.Lock()

    async def one(check_in: date) -> DateOffers:
        nonlocal done
        try:
            result = await collector.channel_offers(
                token, check_in=check_in, nights=nights, adults=adults, currency=calendar.currency
            )
            outcome = DateOffers(check_in=check_in, outcome=DateOutcome.OK, result=result)
        except Blocked as error:
            outcome = DateOffers(check_in=check_in, outcome=DateOutcome.BLOCKED, error=str(error))
        except NotFound as error:
            outcome = DateOffers(check_in=check_in, outcome=DateOutcome.NOT_FOUND, error=str(error))
        except ParseError as error:
            outcome = DateOffers(check_in=check_in, outcome=DateOutcome.PARSE_ERROR, error=str(error))
        except Exception as error:  # noqa: BLE001 - every date gets an outcome
            outcome = DateOffers(check_in=check_in, outcome=DateOutcome.FAILED, error=f"{type(error).__name__}: {error}")
        async with lock:
            done += 1
            if on_progress:
                await on_progress(done, total_steps, f"offers {check_in.isoformat()}: {outcome.outcome.value}")
        return outcome

    # The request policy caps how many of these are in flight; gather only queues them.
    offers = list(await asyncio.gather(*(one(day) for day in dates)))
    ok = sum(item.outcome is DateOutcome.OK for item in offers)
    name = next((item.result.hotel_name for item in offers if item.result and item.result.hotel_name), None)
    return RateShopResult(
        hotel_id=token,
        hotel_name=name,
        calendar=calendar,
        offers=offers,
        offer_dates_ok=ok,
        offer_dates_failed=len(offers) - ok,
        elapsed_seconds=round(time.monotonic() - started, 2),
        requests_sent=collector.requests_sent - requests_before,
    )

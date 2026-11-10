"""``hotelrate-mcp find`` and ``hotelrate-mcp shop``: Google Hotels rate shop from the command line.

find  lists candidate hotels for a name, so the right property token can be picked.
shop  fetches a hotel's price calendar and, optionally, every channel's offers on sample or given dates.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import sys
from datetime import date
from pathlib import Path
from typing import Any

from hotelrate.config import get_settings
from hotelrate.rateshop import (
    CollectorError,
    GoogleHotelsCollector,
    RateShopResult,
    RawResponse,
    rate_shop,
    sample_dates,
)

CALENDAR_COLUMNS = [
    "check_in", "check_out", "status", "rate_per_night", "total", "total_before_taxes", "taxes_and_fees",
    "currency", "cheapest",
]
OFFER_COLUMNS = [
    "check_in", "channel", "is_official_site", "is_sponsored", "room", "rate_plan", "free_cancellation",
    "cancellation_deadline", "breakfast", "price_per_night", "price_total", "currency", "is_member_rate",
    "non_member_price_per_night",
]


def add_find_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("name", help='Hotel name, e.g. "The Savoy"')
    parser.add_argument("--city", default="", help="City, to narrow the search")
    parser.add_argument("--max", type=int, default=5, dest="max_candidates", help="Candidates to list (default 5)")


def add_shop_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("hotel", help="Property token (from find), Google Hotels link, or name with city")
    parser.add_argument("--start", type=date.fromisoformat, default=None, help="First check-in date (default today)")
    parser.add_argument("--days", type=int, default=365, help="Check-in dates in the calendar (default 365)")
    parser.add_argument("--nights", type=int, default=1)
    parser.add_argument("--adults", type=int, default=2)
    parser.add_argument("--currency", default=None, help="ISO 4217 code (default DEFAULT_CURRENCY)")
    parser.add_argument(
        "--offers",
        choices=["none", "sample"],
        default="none",
        help="'sample' adds every channel's offers for one week a month (docs/DESIGN.md section 6)",
    )
    parser.add_argument(
        "--offer-date", type=date.fromisoformat, action="append", default=[], help="Extra offers date (repeatable)"
    )
    parser.add_argument("--out", type=Path, default=None, help="Write the full result as JSON to this file")
    parser.add_argument("--csv-dir", type=Path, default=None, help="Write calendar.csv and offers.csv here")
    parser.add_argument("--raw", type=Path, default=None, help="Append every raw Google answer to this JSON-lines file")


def run_find(args: argparse.Namespace) -> None:
    async def main() -> list[dict[str, Any]]:
        async with GoogleHotelsCollector(get_settings()) as collector:
            found = await collector.resolve_hotel(args.name, args.city, max_candidates=args.max_candidates)
        return [candidate.model_dump(mode="json") for candidate in found]

    try:
        candidates = asyncio.run(main())
    except CollectorError as error:
        _fail(error)
    print(json.dumps(candidates, indent=2))


def run_shop(args: argparse.Namespace) -> None:
    raw_file = args.raw.open("a", encoding="utf-8") if args.raw else None

    def keep_raw(raw: RawResponse) -> None:
        if raw_file:
            raw_file.write(json.dumps(raw.to_fixture(), separators=(",", ":")) + "\n")

    async def progress(done: int, total: int, message: str) -> None:
        print(f"[{done}/{total}] {message}", file=sys.stderr)

    async def main() -> RateShopResult:
        async with GoogleHotelsCollector(get_settings(), on_raw=keep_raw) as collector:
            offer_dates = list(args.offer_date)
            if args.offers == "sample":
                calendar_start = args.start or date.today()
                offer_dates += sample_dates(calendar_start, months=max(1, args.days // 30))
            return await rate_shop(
                collector,
                args.hotel,
                start=args.start,
                days=args.days,
                nights=args.nights,
                adults=args.adults,
                currency=args.currency,
                offer_dates=offer_dates,
                on_progress=progress,
            )

    try:
        result = asyncio.run(main())
    except CollectorError as error:
        _fail(error)
    finally:
        if raw_file:
            raw_file.close()

    if args.out:
        args.out.write_text(result.model_dump_json(indent=2), encoding="utf-8")
    if args.csv_dir:
        write_csv(result, args.csv_dir)
    print(json.dumps(summary(result), indent=2))


def summary(result: RateShopResult) -> dict[str, Any]:
    calendar = result.calendar
    return {
        "hotel_id": result.hotel_id,
        "hotel_name": result.hotel_name,
        "matched_by": calendar.matched_by,
        "currency": calendar.currency,
        "calendar": {
            "start": calendar.start.isoformat(),
            "days_requested": calendar.days_requested,
            "horizon": calendar.horizon.isoformat() if calendar.horizon else None,
            "priced_days": calendar.priced_days,
            "no_price_days": calendar.no_price_days,
            "not_offered_days": calendar.not_offered_days,
            "lowest_total": calendar.lowest_total,
            "median_total": calendar.median_total,
            "highest_total": calendar.highest_total,
        },
        "offer_dates_ok": result.offer_dates_ok,
        "offer_dates_failed": result.offer_dates_failed,
        "requests_sent": result.requests_sent,
        "elapsed_seconds": result.elapsed_seconds,
    }


def write_csv(result: RateShopResult, directory: Path) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "calendar.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=CALENDAR_COLUMNS)
        writer.writeheader()
        for day in result.calendar.days:
            writer.writerow(day.model_dump(mode="json"))
    with (directory / "offers.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=OFFER_COLUMNS, extrasaction="ignore")
        writer.writeheader()
        for dated in result.offers:
            for offer in dated.result.offers if dated.result else []:
                writer.writerow({"check_in": dated.check_in.isoformat(), **offer.model_dump(mode="json")})


def _fail(error: CollectorError) -> None:
    print(f"error: {type(error).__name__}: {error}", file=sys.stderr)
    raise SystemExit(1)

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hotelrate.collectors import AgodaCollector, BookingCollector, GoogleHotelsCollector, TripCollector
from hotelrate.config import get_settings
from hotelrate.schemas import SearchRequest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect captured live JSON payloads by platform")
    parser.add_argument("--platform", required=True, choices=["trip", "booking", "google_hotels", "agoda"])
    parser.add_argument("--platform-hotel-id", required=True)
    parser.add_argument("--platform-url", default=None)
    parser.add_argument("--checkin", default=None, help="YYYY-MM-DD")
    parser.add_argument("--nights", type=int, default=1)
    parser.add_argument("--adults", type=int, default=2)
    parser.add_argument("--children", type=int, default=0)
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--settle-ms", type=int, default=3500)
    parser.add_argument("--max-offers", type=int, default=10)
    parser.add_argument("--headed", action="store_true", help="Run browser with UI")
    return parser.parse_args()


def collector_factory(platform: str):
    mapping = {
        "trip": TripCollector,
        "booking": BookingCollector,
        "google_hotels": GoogleHotelsCollector,
        "agoda": AgodaCollector,
    }
    return mapping[platform]


def top_keys(data: Any) -> list[str]:
    if isinstance(data, dict):
        return list(data.keys())[:10]
    if isinstance(data, list) and data and isinstance(data[0], dict):
        return list(data[0].keys())[:10]
    return []


async def main() -> None:
    args = parse_args()

    checkin = date.fromisoformat(args.checkin) if args.checkin else date.today() + timedelta(days=7)
    checkout = checkin + timedelta(days=args.nights)

    settings = get_settings().model_copy(update={"demo_mode": False, "headless": not args.headed})
    collector_cls = collector_factory(args.platform)
    collector = collector_cls(settings)

    request = SearchRequest(
        platform_hotel_id=args.platform_hotel_id,
        platform_url=args.platform_url,
        checkin=checkin,
        checkout=checkout,
        adults=args.adults,
        children=args.children,
        currency=args.currency,
    )

    if args.platform_url:
        url = args.platform_url
    elif args.platform_hotel_id.startswith("http://") or args.platform_hotel_id.startswith("https://"):
        request = request.model_copy(update={"platform_url": args.platform_hotel_id, "platform_hotel_id": "manual"})
        url = args.platform_hotel_id
    else:
        url = collector._build_search_url(request)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=not args.headed)
        context = await browser.new_context(locale="en-US")
        page = await context.new_page()

        payloads = await collector.capture_json_payloads(
            page=page,
            url=url,
            response_patterns=getattr(collector, "RESPONSE_PATTERNS", collector.DEFAULT_RESPONSE_PATTERNS),
            settle_ms=args.settle_ms,
        )

        offers = collector.parse_payloads_to_offers(
            request=request,
            payloads=payloads,
            max_offers=args.max_offers,
        )

        if not offers:
            offers = await collector.parse_ld_json_fallback(
                page=page,
                request=request,
                max_offers=args.max_offers,
            )

        await browser.close()

    print(f"platform={args.platform} url={url}")
    print(f"captured_payloads={len(payloads)} parsed_offers={len(offers)}")

    for idx, payload in enumerate(payloads[:15], start=1):
        keys = ", ".join(top_keys(payload.get("data")))
        print(f"[{idx}] {payload.get('status')} {payload.get('url')}")
        if keys:
            print(f"    keys: {keys}")

    if offers:
        print("\nSample offers:")
        for idx, offer in enumerate(offers, start=1):
            print(
                f"[{idx}] room={offer.room_name} final={offer.final_price} {offer.currency} "
                f"meal={offer.meal_plan or '-'}"
            )


if __name__ == "__main__":
    asyncio.run(main())

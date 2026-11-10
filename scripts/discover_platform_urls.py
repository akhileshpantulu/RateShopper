from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hotelrate.config import get_settings
from hotelrate.schemas import Platform
from hotelrate.services.url_discovery import UrlDiscoveryService


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Discover platform detail URLs by hotel keyword.")
    parser.add_argument("--hotel", required=True)
    parser.add_argument("--city", default="")
    parser.add_argument("--checkin", required=True, help="YYYY-MM-DD")
    parser.add_argument("--checkout", required=True, help="YYYY-MM-DD")
    parser.add_argument("--adults", type=int, default=2)
    parser.add_argument("--children", type=int, default=0)
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--locale", default="en-US", help="BCP 47 locale for site hints and discovery")
    parser.add_argument("--market", default="", help="ISO 3166-1 alpha-2 for Google gl= (optional)")
    parser.add_argument("--top", type=int, default=5)
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    checkin = date.fromisoformat(args.checkin).isoformat()
    checkout = date.fromisoformat(args.checkout).isoformat()
    market_arg = args.market.strip().upper()
    market = market_arg if len(market_arg) == 2 and market_arg.isalpha() else None

    settings = get_settings()
    discovery = UrlDiscoveryService(settings)
    platforms = [Platform.TRIP, Platform.BOOKING, Platform.AGODA, Platform.GOOGLE_HOTELS]

    for platform in platforms:
        urls = await discovery.discover(
            platform=platform,
            hotel_name=args.hotel,
            city=args.city,
            checkin=checkin,
            checkout=checkout,
            adults=args.adults,
            children=args.children,
            currency=args.currency,
            known_url=None,
            locale=args.locale,
            market=market,
        )
        print(f"\n[{platform.value}] candidates={len(urls)}")
        if not urls:
            print("  no candidates")
            continue
        for idx, url in enumerate(urls[: args.top], start=1):
            detail_tag = "detail" if discovery.is_detail_url(platform, url) else "search/list"
            print(f"  {idx}. ({detail_tag}) {url}")


if __name__ == "__main__":
    asyncio.run(main())

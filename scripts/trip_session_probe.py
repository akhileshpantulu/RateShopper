from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hotelrate.collectors import TripCollector
from hotelrate.config import get_settings
from hotelrate.schemas import SearchRequest


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Probe Trip collector with persistent profile.")
    parser.add_argument(
        "--url",
        default=(
            "https://www.trip.com/hotels/detail/?cityEnName=Shanghai&cityId=2&hotelId=114516981"
            "&checkIn=2026-03-02&checkOut=2026-03-03&adult=2&children=0&curr=USD&barcurr=USD"
        ),
    )
    parser.add_argument("--hotel-id", default="114516981")
    parser.add_argument("--checkin", default="2026-03-02")
    parser.add_argument("--checkout", default="2026-03-03")
    parser.add_argument(
        "--user-data-dir",
        default=os.environ.get("PLAYWRIGHT_USER_DATA_DIR", str(ROOT / ".playwright" / "trip-profile")),
    )
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    settings = get_settings().model_copy(
        update={
            "demo_mode": False,
            "headless": True,
            "playwright_user_data_dir": str(Path(args.user_data_dir).expanduser().resolve()),
        }
    )
    collector = TripCollector(settings)
    request = SearchRequest(
        platform_hotel_id=args.hotel_id,
        platform_url=args.url,
        checkin=date.fromisoformat(args.checkin),
        checkout=date.fromisoformat(args.checkout),
        adults=2,
        children=0,
        currency="USD",
    )
    offers = await collector.fetch_rates(request)
    print(f"offers={len(offers)}")
    for idx, offer in enumerate(offers[:8], start=1):
        mode = offer.raw_payload.get("mode", "parsed")
        print(f"[{idx}] {offer.room_name} | {offer.final_price} {offer.currency} | mode={mode}")


if __name__ == "__main__":
    asyncio.run(main())

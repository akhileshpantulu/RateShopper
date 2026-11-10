from __future__ import annotations

import asyncio
import json
import sys
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hotelrate.config import get_settings
from hotelrate.schemas import LiveQuoteRequest, Platform
from hotelrate.services import LiveQuoteService


SCENARIOS = [
    {
        "name": "trip_single",
        "request": LiveQuoteRequest(
            hotel_name="Trip Shanghai Hotel 114516981",
            city="Shanghai",
            checkin=date(2026, 3, 1),
            checkout=date(2026, 3, 2),
            adults=2,
            children=0,
            currency="USD",
            platforms=[Platform.TRIP],
            known_urls={
                Platform.TRIP.value: (
                    "https://www.trip.com/hotels/detail/?cityEnName=Shanghai&cityId=2&hotelId=114516981"
                    "&checkIn=2026-03-01&checkOut=2026-03-02&adult=2&children=0&crn=1&ages=&curr=USD"
                    "&barcurr=USD&hoteluniquekey=H4sIAAAAAAAA_-M6y8TFJMEkdYyJ4-jMBXvYhBgNDHaJWNyKcIzpP_RVw63JwZMBBBqaHQJ4IqYceD6NPdFyy4myfeqBDo2MxiBQbOvQxcgKlprB-PX4WbONjLZc1xcXzHV22MHIdIIxdAFT85mzZqdYWDn2sEiwXGLZzBhdrZSdWqlkZaKjVJJZkpOqZKUU5hiipKOUklqcDOQAWYm5-aV5JUC2qZ65EZBfkljhmQLWkZyYk1yak1iSGlJZANRppqOUWexcUpRZEJSam1lSkgpUlZaYU5wKEg9KLQbKJIMFlfyAxhRBBTLz8yDaDVHEwhJzSlMhDgBa6JYKtcOwNvYRC1N07CcWhl8sDE2sDF2sTBxck1hZObrdJVh2sbJFBpp6uxpdYI2XUjY0MDAwNTQ2NdU1SLRMNLMwMtQ1sTQyNjLQNTQwN7awMDUy0th47frhU8ZGVlKQYFNg1BKIDwmOiEipzEi1yLAsMSoyNmA8xShlaG5uaGZhYGhiYWxqoGdmamps4mgIdJVxpgdjEJuJo7m5kXOUDRezs1-kIDiaGD7YSzGHBrsoHmpbHn6qSNpBCyRnCJMLhDEiYfJJrKl5uhERGTtEChi7GLkFGD0YIxgrGBsYGVcxMmxgZNzB-B8GGF8xgowGAMbLcoAyAgAA"
                    "&masterhotelid_tracelogid=100051355-0a9a6821-492320-107388522&detailFilters=17%7C1%7E17%7E1*undefined%7E%7E*80%7C0%7C1%7E80%7E0*29%7C1%7E29%7E1%7C2"
                    "&hotelType=normal&display=exavg&subStamp=650&isCT=true&isFlexible=F&locale=en-XX"
                )
            },
        ),
    },
    {
        "name": "agoda_single",
        "request": LiveQuoteRequest(
            hotel_name="Tru by Hilton Las Vegas Airport",
            city="Las Vegas",
            checkin=date(2026, 3, 10),
            checkout=date(2026, 3, 11),
            adults=2,
            children=0,
            currency="USD",
            platforms=[Platform.AGODA],
            known_urls={
                Platform.AGODA.value: (
                    "https://www.agoda.com/zh-hk/tru-by-hilton-las-vegas-airport/hotel/las-vegas-nv-us.html"
                    "?countryId=181&finalPriceView=1&isShowMobileAppPrice=false&cid=-1&numberOfBedrooms=&familyMode=false"
                    "&adults=2&children=0&rooms=1&maxRooms=0&checkIn=2026-03-10&isCalendarCallout=false&childAges="
                    "&numberOfGuest=0&missingChildAges=false&travellerType=1&showReviewSubmissionEntry=false&currencyCode=USD"
                    "&isFreeOccSearch=false&tspTypes=2&los=1&searchrequestid=e2f7461c-2336-4bb7-8bfc-7b30c3ac9bc5"
                )
            },
        ),
    },
    {
        "name": "booking_single",
        "request": LiveQuoteRequest(
            hotel_name="Hôtel Confidentiel - Louvre",
            city="Paris",
            checkin=date(2026, 3, 21),
            checkout=date(2026, 3, 22),
            adults=2,
            children=0,
            currency="USD",
            platforms=[Platform.BOOKING],
            known_urls={
                Platform.BOOKING.value: (
                    "https://www.booking.com/hotel/fr/confidential-room-3-a-stones-throw-from-the-louvre.zh-cn.html"
                    "?aid=304142&label=gen173bo-10CAEoggI46AdIM1gDaDGIAQGYATO4AQfIAQzYAQPoAQH4AQGIAgGYAgKoAgG4Ao7rj80GwAIB0gIkMGMwYjYwYzMtYThlOC00ODU0LTgwMmItOWZiMzI3MmQ1MmMz2AIB4AIB"
                    "&sid=0fd8f33abf4071ef329ffaab04a20973&all_sr_blocks=1478273306_428699806_2_0_0&checkin=2026-03-21"
                    "&checkout=2026-03-22&dest_id=-1456928&dest_type=city&dist=0&group_adults=2&group_children=0&hapos=1"
                    "&highlighted_blocks=1478273306_428699806_2_0_0&hpos=1&matching_block_id=1478273306_428699806_2_0_0"
                    "&no_rooms=1&req_adults=2&req_children=0&room1=A%2CA&sb_price_type=total&sr_order=popularity"
                    "&sr_pri_blocks=1478273306_428699806_2_0_0__58478&srepoch=1772352922&srpvid=7f183a0bc6c0003b&type=total&ucfs=1&"
                )
            },
        ),
    },
    {
        "name": "google_single",
        "request": LiveQuoteRequest(
            hotel_name="Atour Hotel Shanghai HuangPu Xintiandi Metro Station",
            city="Shanghai",
            checkin=date(2026, 3, 9),
            checkout=date(2026, 3, 10),
            adults=2,
            children=0,
            currency="USD",
            platforms=[Platform.GOOGLE_HOTELS],
            known_urls={
                Platform.GOOGLE_HOTELS.value: (
                    "https://www.google.com/travel/search?q=Atour%20Hotel%20Shanghai%20HuangPu%20Xintiandi%20Metro%20Station"
                    "&g2lb=4965990%2C72471280%2C72560029%2C72573224%2C72647020%2C72686036%2C72803964%2C72882230%2C72958624%2C73059275%2C73064764%2C73249150"
                    "&hl=zh-Hans-US&gl=us&ssta=1&ts=CAEaRwopEicyJTB4MzViMjcwNzZkOTRjODU1ZjoweDUzMGRhMGM0ZGFmZmU3NzUSGhIUCgcI6g8QAxgJEgcI6g8QAxgKGAEyAhAA"
                    "&qs=CAEyE0Nnb0k5YzdfMTgyWTZJWlRFQUU4AkIJCXXn_9rEoA1TQgkJdef_2sSgDVM&ap=ugEGcHJpY2Vz"
                    "&ved=0CAAQ5JsGahcKEwjguYymof6SAxUAAAAAHQAAAAAQDA&ictx=111"
                )
            },
        ),
    },
    {
        "name": "cross_platform_demo",
        "request": LiveQuoteRequest(
            hotel_name="Atour Hotel Shanghai HuangPu Xintiandi Metro Station",
            city="Shanghai",
            checkin=date(2026, 3, 9),
            checkout=date(2026, 3, 10),
            adults=2,
            children=0,
            currency="USD",
            platforms=[Platform.TRIP, Platform.BOOKING, Platform.GOOGLE_HOTELS, Platform.AGODA],
            known_urls={},
            probe_days_ahead=12,
            probe_step_days=3,
        ),
    },
]


async def main() -> None:
    settings = get_settings().model_copy(update={"demo_mode": False})
    service = LiveQuoteService(settings)
    outputs = []

    for item in SCENARIOS:
        name = item["name"]
        req = item["request"]
        print(f"run scenario: {name}")
        result = await service.quote(req)
        payload = result.model_dump(mode="json")
        outputs.append({"scenario": name, "result": payload})

    report_path = ROOT / "reports" / "nightly_quote_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(outputs, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"saved: {report_path}")


if __name__ == "__main__":
    asyncio.run(main())

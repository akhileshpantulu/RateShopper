from __future__ import annotations

import asyncio
import sys
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import urlparse

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hotelrate.collectors import AgodaCollector, BookingCollector, GoogleHotelsCollector, TripCollector
from hotelrate.config import get_settings
from hotelrate.schemas import SearchRequest


@dataclass(slots=True)
class LiveTask:
    platform: str
    hotel_id: str
    url: str
    checkin: date
    checkout: date
    adults: int = 2
    children: int = 0
    currency: str = "USD"
    locale: str = "en-US"
    market: str | None = None


@dataclass(slots=True)
class CrawlOutcome:
    request: SearchRequest
    url: str
    payloads: list[dict]
    offers: list


TASKS: list[LiveTask] = [
    LiveTask(
        platform="trip",
        hotel_id="114516981",
        url=(
            "https://www.trip.com/hotels/detail/?cityEnName=Shanghai&cityId=2&hotelId=114516981"
            "&checkIn=2026-03-01&checkOut=2026-03-02&adult=2&children=0&crn=1&ages=&curr=USD"
            "&barcurr=USD&hoteluniquekey=H4sIAAAAAAAA_-M6y8TFJMEkdYyJ4-jMBXvYhBgNDHaJWNyKcIzpP_RVw63JwZMBBBqaHQJ4IqYceD6NPdFyy4myfeqBDo2MxiBQbOvQxcgKlprB-PX4WbONjLZc1xcXzHV22MHIdIIxdAFT85mzZqdYWDn2sEiwXGLZzBhdrZSdWqlkZaKjVJJZkpOqZKUU5hiipKOUklqcDOQAWYm5-aV5JUC2qZ65EZBfkljhmQLWkZyYk1yak1iSGlJZANRppqOUWexcUpRZEJSam1lSkgpUlZaYU5wKEg9KLQbKJIMFlfyAxhRBBTLz8yDaDVHEwhJzSlMhDgBa6JYKtcOwNvYRC1N07CcWhl8sDE2sDF2sTBxck1hZObrdJVh2sbJFBpp6uxpdYI2XUjY0MDAwNTQ2NdU1SLRMNLMwMtQ1sTQyNjLQNTQwN7awMDUy0th47frhU8ZGVlKQYFNg1BKIDwmOiEipzEi1yLAsMSoyNmA8xShlaG5uaGZhYGhiYWxqoGdmamps4mgIdJVxpgdjEJuJo7m5kXOUDRezs1-kIDiaGD7YSzGHBrsoHmpbHn6qSNpBCyRnCJMLhDEiYfJJrKl5uhERGTtEChi7GLkFGD0YIxgrGBsYGVcxMmxgZNzB-B8GGF8xgowGAMbLcoAyAgAA"
            "&masterhotelid_tracelogid=100051355-0a9a6821-492320-107388522&detailFilters=17%7C1%7E17%7E1*undefined%7E%7E*80%7C0%7C1%7E80%7E0*29%7C1%7E29%7E1%7C2"
            "&hotelType=normal&display=exavg&subStamp=650&isCT=true&isFlexible=F&locale=en-XX"
        ),
        checkin=date(2026, 3, 1),
        checkout=date(2026, 3, 2),
    ),
    LiveTask(
        platform="agoda",
        hotel_id="tru-by-hilton-las-vegas-airport",
        url=(
            "https://www.agoda.com/zh-hk/tru-by-hilton-las-vegas-airport/hotel/las-vegas-nv-us.html"
            "?countryId=181&finalPriceView=1&isShowMobileAppPrice=false&cid=-1&numberOfBedrooms=&familyMode=false"
            "&adults=2&children=0&rooms=1&maxRooms=0&checkIn=2026-03-10&isCalendarCallout=false&childAges="
            "&numberOfGuest=0&missingChildAges=false&travellerType=1&showReviewSubmissionEntry=false&currencyCode=USD"
            "&isFreeOccSearch=false&tspTypes=2&los=1&searchrequestid=e2f7461c-2336-4bb7-8bfc-7b30c3ac9bc5"
        ),
        checkin=date(2026, 3, 10),
        checkout=date(2026, 3, 11),
    ),
    LiveTask(
        platform="booking",
        hotel_id="fr/confidential-room-3-a-stones-throw-from-the-louvre.zh-cn",
        url=(
            "https://www.booking.com/hotel/fr/confidential-room-3-a-stones-throw-from-the-louvre.zh-cn.html"
            "?aid=304142&label=gen173bo-10CAEoggI46AdIM1gDaDGIAQGYATO4AQfIAQzYAQPoAQH4AQGIAgGYAgKoAgG4Ao7rj80GwAIB0gIkMGMwYjYwYzMtYThlOC00ODU0LTgwMmItOWZiMzI3MmQ1MmMz2AIB4AIB"
            "&sid=0fd8f33abf4071ef329ffaab04a20973&all_sr_blocks=1478273306_428699806_2_0_0&checkin=2026-03-21"
            "&checkout=2026-03-22&dest_id=-1456928&dest_type=city&dist=0&group_adults=2&group_children=0&hapos=1"
            "&highlighted_blocks=1478273306_428699806_2_0_0&hpos=1&matching_block_id=1478273306_428699806_2_0_0"
            "&no_rooms=1&req_adults=2&req_children=0&room1=A%2CA&sb_price_type=total&sr_order=popularity"
            "&sr_pri_blocks=1478273306_428699806_2_0_0__58478&srepoch=1772352922&srpvid=7f183a0bc6c0003b&type=total&ucfs=1&"
        ),
        checkin=date(2026, 3, 21),
        checkout=date(2026, 3, 22),
    ),
    LiveTask(
        platform="google_hotels",
        hotel_id="Atour Hotel Shanghai HuangPu Xintiandi Metro Station",
        url=(
            "https://www.google.com/travel/search?q=Atour%20Hotel%20Shanghai%20HuangPu%20Xintiandi%20Metro%20Station"
            "&g2lb=4965990%2C72471280%2C72560029%2C72573224%2C72647020%2C72686036%2C72803964%2C72882230%2C72958624%2C73059275%2C73064764%2C73249150"
            "&hl=zh-Hans-US&gl=us&ssta=1&ts=CAEaRwopEicyJTB4MzViMjcwNzZkOTRjODU1ZjoweDUzMGRhMGM0ZGFmZmU3NzUSGhIUCgcI6g8QAxgJEgcI6g8QAxgKGAEyAhAA"
            "&qs=CAEyE0Nnb0k5YzdfMTgyWTZJWlRFQUU4AkIJCXXn_9rEoA1TQgkJdef_2sSgDVM&ap=ugEGcHJpY2Vz"
            "&ved=0CAAQ5JsGahcKEwjguYymof6SAxUAAAAAHQAAAAAQDA&ictx=111"
        ),
        checkin=date(2026, 3, 9),
        checkout=date(2026, 3, 10),
    ),
]


def build_collector(platform: str):
    settings = get_settings().model_copy(update={"demo_mode": False, "headless": True})
    mapping = {
        "trip": TripCollector,
        "agoda": AgodaCollector,
        "booking": BookingCollector,
        "google_hotels": GoogleHotelsCollector,
    }
    return mapping[platform](settings)


def build_diagnosis(platform: str, payloads: list[dict]) -> str:
    if platform == "trip":
        for payload in payloads:
            if "getHotelRoomListOversea" not in payload.get("url", ""):
                continue
            data = payload.get("data")
            if not isinstance(data, dict):
                continue
            body = data.get("data")
            if not isinstance(body, dict):
                continue
            code = body.get("htlSpiderActionErrorCode")
            if code is not None:
                return f"trip room api error code={code}"
        return "no getHotelRoomListOversea response with room data"

    if platform == "agoda":
        for payload in payloads:
            if "/api/v1/property/room-grid" not in payload.get("url", ""):
                continue
            data = payload.get("data")
            if not isinstance(data, dict):
                continue
            sold_out = data.get("isSoldOut")
            rooms = data.get("rooms")
            room_count = len(rooms) if isinstance(rooms, list) else 0
            return f"room-grid sold_out={sold_out} rooms={room_count}"
        return "no room-grid response captured"

    if platform == "booking":
        graphql_keys: list[str] = []
        for payload in payloads:
            if "/dml/graphql" not in payload.get("url", ""):
                continue
            data = payload.get("data")
            if not isinstance(data, dict):
                continue
            body = data.get("data")
            if not isinstance(body, dict):
                continue
            graphql_keys.extend(list(body.keys()))
        if graphql_keys:
            uniq = ",".join(sorted(set(graphql_keys)))
            return f"graphql keys={uniq}"
        return "no graphql data payload captured"

    if platform == "google_hotels":
        domains: dict[str, int] = {}
        for payload in payloads:
            url = payload.get("url", "")
            domain = urlparse(url).netloc
            if not domain:
                continue
            domains[domain] = domains.get(domain, 0) + 1
        if domains:
            summary = ", ".join(f"{k}:{v}" for k, v in sorted(domains.items(), key=lambda x: -x[1])[:4])
            return f"payload domains={summary}"
        return "no payload domain summary"

    return "no diagnosis"


def structured_offers(offers: list) -> list:
    fallback_modes = {"text_fallback", "url_fallback", "demo", "dom_room_table_url_price"}
    return [offer for offer in offers if offer.raw_payload.get("mode") not in fallback_modes]


async def crawl_once(
    *,
    collector,
    task: LiveTask,
    checkin: date,
    checkout: date,
    include_text_fallback: bool,
) -> CrawlOutcome:
    request = SearchRequest(
        platform_hotel_id=task.hotel_id,
        platform_url=task.url,
        checkin=checkin,
        checkout=checkout,
        adults=task.adults,
        children=task.children,
        currency=task.currency,
        locale=task.locale,
        market=task.market,
    )

    url = collector._build_search_url(request)

    async with async_playwright() as pw:
        browser, context = await collector.open_playwright_context(pw, request)
        page = await context.new_page()

        payloads = await collector.capture_json_payloads(
            page=page,
            url=url,
            response_patterns=getattr(collector, "RESPONSE_PATTERNS", collector.DEFAULT_RESPONSE_PATTERNS),
            settle_ms=4000,
            after_load=getattr(collector, "_after_load_interactions", None),
        )
        parser = getattr(collector, "_parse_structured_offers", None)
        if callable(parser):
            offers = parser(request=request, payloads=payloads)
        else:
            offers = collector.parse_payloads_to_offers(request=request, payloads=payloads, max_offers=10)
        if not offers:
            offers = await collector.parse_ld_json_fallback(page=page, request=request, max_offers=10)
        dom_room_table = getattr(collector, "_parse_room_table_dom_fallback", None)
        if not offers and callable(dom_room_table):
            offers = await dom_room_table(page=page, request=request)
        dom_card = getattr(collector, "_parse_aria_label_card_offers", None)
        if not offers and callable(dom_card):
            offers = await dom_card(page=page, request=request)
        below_fold = getattr(collector, "_parse_below_fold_payloads", None)
        if not offers and callable(below_fold):
            offers = below_fold(request=request, payloads=payloads)
        url_fallback = getattr(collector, "_parse_url_price_fallback", None)
        if not offers and callable(url_fallback):
            offers = url_fallback(request)
        if include_text_fallback and not offers:
            offers = await collector.parse_text_price_fallback(page=page, request=request, max_offers=10)

        await collector.close_playwright_context(browser=browser, context=context)

    return CrawlOutcome(request=request, url=url, payloads=payloads, offers=offers)


async def probe_available_window(
    *,
    collector,
    task: LiveTask,
    days_ahead: int = 6,
    step_days: int = 3,
) -> CrawlOutcome | None:
    nights = (task.checkout - task.checkin).days
    for offset in range(step_days, days_ahead + 1, step_days):
        probe_checkin = task.checkin + timedelta(days=offset)
        probe_checkout = probe_checkin + timedelta(days=nights)
        outcome = await crawl_once(
            collector=collector,
            task=task,
            checkin=probe_checkin,
            checkout=probe_checkout,
            include_text_fallback=False,
        )
        if structured_offers(outcome.offers):
            return outcome
    return None


async def run_task(task: LiveTask) -> None:
    collector = build_collector(task.platform)
    outcome = await crawl_once(
        collector=collector,
        task=task,
        checkin=task.checkin,
        checkout=task.checkout,
        include_text_fallback=True,
    )

    if task.platform in {"agoda", "booking"} and not structured_offers(outcome.offers):
        probe = await probe_available_window(collector=collector, task=task)
        if probe is not None:
            outcome = probe

    print(f"\n=== {task.platform} ===")
    print(f"url: {outcome.url}")
    print(f"date_window: {outcome.request.checkin} -> {outcome.request.checkout}")
    print(f"captured_payloads: {len(outcome.payloads)}")
    print(f"parsed_offers: {len(outcome.offers)}")
    print(f"structured_offers: {len(structured_offers(outcome.offers))}")
    print(f"diagnosis: {build_diagnosis(task.platform, outcome.payloads)}")
    for idx, offer in enumerate(outcome.offers[:5], start=1):
        mode = offer.raw_payload.get("mode", "parsed")
        print(
            f"[{idx}] room={offer.room_name} final={offer.final_price} {offer.currency} "
            f"meal={offer.meal_plan or '-'} mode={mode}"
        )


async def main() -> None:
    for task in TASKS:
        try:
            await run_task(task)
        except Exception as exc:
            print(f"\n=== {task.platform} FAILED ===")
            print(f"error: {exc}")


if __name__ == "__main__":
    asyncio.run(main())

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, timedelta

from hotelrate.collectors import AgodaCollector, BookingCollector, GoogleHotelsCollector, TripCollector
from hotelrate.config import Settings, get_settings
from hotelrate.core import normalize_offer
from hotelrate.infra.db import SessionLocal
from hotelrate.infra.models import HotelMapping
from hotelrate.infra.repository import RateRepository
from hotelrate.schemas import NormalizedSnapshot, Platform, SearchRequest


@dataclass(slots=True)
class CrawlWindow:
    checkin: date
    checkout: date
    adults: int
    children: int
    currency: str = "USD"
    locale: str = "en-US"
    market: str | None = None


class RateCrawlerService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.logger = logging.getLogger(self.__class__.__name__)
        self.collectors = {
            Platform.TRIP: TripCollector(self.settings),
            Platform.BOOKING: BookingCollector(self.settings),
            Platform.GOOGLE_HOTELS: GoogleHotelsCollector(self.settings),
            Platform.AGODA: AgodaCollector(self.settings),
        }

    async def crawl_once(self) -> None:
        window = self._build_default_window()
        self.logger.info(
            "start crawl cycle checkin=%s checkout=%s adults=%d children=%d",
            window.checkin,
            window.checkout,
            window.adults,
            window.children,
        )

        for platform, collector in self.collectors.items():
            async with SessionLocal() as session:
                repo = RateRepository(session)
                mappings = await repo.list_hotel_mappings(
                    platform=platform,
                    city=self.settings.target_city or None,
                )
                if not mappings:
                    self.logger.info("skip platform=%s because no mappings", platform.value)
                    continue

                job = await repo.create_job_log(platform=platform, target_count=len(mappings))

                success_count = 0
                failed_count = 0
                inserted = 0
                pending_snapshots: list[NormalizedSnapshot] = []

                semaphore = asyncio.Semaphore(self.settings.crawl_concurrency)
                tasks = [
                    self._crawl_one_mapping(
                        semaphore=semaphore,
                        collector=collector,
                        mapping=mapping,
                        window=window,
                    )
                    for mapping in mappings
                ]

                results = await asyncio.gather(*tasks, return_exceptions=False)

                for ok, normalized_rows, error_msg in results:
                    if ok:
                        success_count += 1
                        pending_snapshots.extend(normalized_rows)
                        if len(pending_snapshots) >= self.settings.crawl_insert_batch_size:
                            inserted += await repo.insert_snapshots(pending_snapshots)
                            pending_snapshots.clear()
                    else:
                        failed_count += 1
                        self.logger.warning(
                            "mapping crawl failed platform=%s error=%s",
                            platform.value,
                            error_msg,
                        )

                if pending_snapshots:
                    inserted += await repo.insert_snapshots(pending_snapshots)
                    pending_snapshots.clear()
                status = "success" if failed_count == 0 else "partial"

                await repo.finish_job_log(
                    job=job,
                    success_count=success_count,
                    failed_count=failed_count,
                    status=status,
                    error_message=None if failed_count == 0 else f"failed={failed_count}",
                )

                self.logger.info(
                    "platform=%s targets=%d success=%d failed=%d inserted=%d",
                    platform.value,
                    len(mappings),
                    success_count,
                    failed_count,
                    inserted,
                )

    async def _crawl_one_mapping(
        self,
        *,
        semaphore: asyncio.Semaphore,
        collector,
        mapping: HotelMapping,
        window: CrawlWindow,
    ) -> tuple[bool, list[NormalizedSnapshot], str | None]:
        request = SearchRequest(
            platform_hotel_id=mapping.platform_hotel_id,
            platform_url=mapping.platform_url,
            checkin=window.checkin,
            checkout=window.checkout,
            adults=window.adults,
            children=window.children,
            currency=window.currency,
            locale=window.locale,
            market=window.market,
        )

        try:
            async with semaphore:
                offers = await collector.fetch_rates(request)
            if not offers:
                return False, [], "no offers parsed"
            normalized = [normalize_offer(mapping.id, offer) for offer in offers]
            return True, normalized, None
        except Exception as exc:
            return False, [], str(exc)

    def _build_default_window(self) -> CrawlWindow:
        checkin = date.today() + timedelta(days=self.settings.default_checkin_offset_days)
        checkout = checkin + timedelta(days=self.settings.default_length_of_stay)
        dm = self.settings.default_market.strip().upper()
        market = dm if len(dm) == 2 and dm.isalpha() else None
        return CrawlWindow(
            checkin=checkin,
            checkout=checkout,
            adults=self.settings.default_adults,
            children=self.settings.default_children,
            locale=self.settings.default_locale,
            market=market,
        )

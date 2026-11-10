from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from hotelrate.collectors import AgodaCollector, BookingCollector, GoogleHotelsCollector, TripCollector
from hotelrate.config import Settings, get_settings
from hotelrate.schemas import (
    BatchQuoteRequest,
    BatchQuoteResponse,
    LiveAttempt,
    LiveOffer,
    LivePlatformResult,
    LiveQuoteRequest,
    LiveQuoteResponse,
    Platform,
    SearchRequest,
)
from hotelrate.services.firecrawl_client import FirecrawlClient
from hotelrate.services.url_mapping_cache import UrlMappingCache, UrlMappingRecord
from hotelrate.services.url_discovery import UrlDiscoveryService


def _html_to_text(html: str) -> str:
    """Strip HTML tags and collapse whitespace for price extraction."""
    if not html:
        return ""
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"\s+", " ", text).strip()
    return text


@dataclass(slots=True)
class AttemptState:
    request: SearchRequest
    url: str
    offers: list
    structured_count: int
    fallback_count: int
    match_score: int
    diagnosis: str


class LiveQuoteService:
    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or get_settings()
        self.logger = logging.getLogger(self.__class__.__name__)
        self.discovery = UrlDiscoveryService(self.settings)
        self.mapping_cache = UrlMappingCache(self.settings.url_mapping_cache_path)
        self.firecrawl = FirecrawlClient(self.settings)
        self.collectors = {
            Platform.TRIP: TripCollector(self.settings),
            Platform.BOOKING: BookingCollector(self.settings),
            Platform.GOOGLE_HOTELS: GoogleHotelsCollector(self.settings),
            Platform.AGODA: AgodaCollector(self.settings),
        }
        self.platform_semaphore = asyncio.Semaphore(self.settings.platform_max_concurrency)
        self.batch_task_semaphore = asyncio.Semaphore(self.settings.batch_task_concurrency)

    @staticmethod
    def _request_contains_google(request: LiveQuoteRequest) -> bool:
        platforms = request.platforms
        if not platforms:
            return True
        return Platform.GOOGLE_HOTELS in platforms

    @staticmethod
    def _partition_platforms(platforms: list[Platform]) -> tuple[list[Platform], list[Platform]]:
        regular: list[Platform] = []
        google: list[Platform] = []
        for platform in platforms:
            if platform == Platform.GOOGLE_HOTELS:
                google.append(platform)
            else:
                regular.append(platform)
        return regular, google

    @classmethod
    def _partition_batch_requests(
        cls,
        requests: list[LiveQuoteRequest],
    ) -> tuple[list[tuple[int, LiveQuoteRequest]], list[tuple[int, LiveQuoteRequest]]]:
        regular: list[tuple[int, LiveQuoteRequest]] = []
        google: list[tuple[int, LiveQuoteRequest]] = []
        for index, request in enumerate(requests):
            if cls._request_contains_google(request):
                google.append((index, request))
            else:
                regular.append((index, request))
        return regular, google

    @staticmethod
    def _build_probe_offsets(
        *,
        platform: Platform,
        probe_days_ahead: int,
        probe_step_days: int,
    ) -> list[int]:
        offsets = [0]
        if platform not in {Platform.AGODA, Platform.BOOKING}:
            return offsets
        if probe_days_ahead <= 0:
            return offsets

        for offset in range(probe_step_days, probe_days_ahead + 1, probe_step_days):
            offsets.append(offset)

        if offsets[-1] != probe_days_ahead:
            offsets.append(probe_days_ahead)
        return offsets

    @staticmethod
    def _build_attempt_plan(
        *,
        urls: list[str],
        offsets: list[int],
        max_attempts: int,
    ) -> list[tuple[str, int]]:
        plan: list[tuple[str, int]] = []
        for offset in offsets:
            for url in urls:
                plan.append((url, offset))
                if len(plan) >= max_attempts:
                    return plan
        return plan

    async def quote(
        self,
        request: LiveQuoteRequest,
        on_progress: Callable[[str, str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> LiveQuoteResponse:
        platforms = request.platforms or list(self.collectors.keys())
        if not platforms:
            return LiveQuoteResponse(request=request, results=[])

        results_by_platform: dict[Platform, LivePlatformResult] = {}
        regular_platforms, google_platforms = self._partition_platforms(platforms)

        async def run_group(group: list[Platform]) -> None:
            if not group:
                return

            semaphore = asyncio.Semaphore(min(self.settings.platform_max_concurrency, len(group)))

            async def run_one(platform: Platform) -> LivePlatformResult:
                async with semaphore:
                    return await self._quote_one_platform(
                        platform=platform,
                        request=request,
                        on_progress=on_progress,
                    )

            group_results = await asyncio.gather(*(run_one(platform) for platform in group))
            for result in group_results:
                results_by_platform[result.platform] = result

        await run_group(regular_platforms)
        await run_group(google_platforms)

        ordered_results = [results_by_platform[platform] for platform in platforms]
        return LiveQuoteResponse(request=request, results=ordered_results)

    async def batch_quote(
        self,
        request: BatchQuoteRequest,
        on_progress: Callable[[str, str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> BatchQuoteResponse:
        """Batch-crawl multiple hotels concurrently."""
        hotel_concurrency = min(request.concurrency, self.settings.batch_hotel_concurrency)
        results: list[LiveQuoteResponse] = []
        errors: list[dict] = []

        async def crawl_group(
            items: list[tuple[int, LiveQuoteRequest]],
            *,
            concurrency: int,
        ) -> list[tuple[int, LiveQuoteResponse | dict]]:
            if not items:
                return []

            group_semaphore = asyncio.Semaphore(concurrency)

            async def crawl_one(req: LiveQuoteRequest, index: int) -> tuple[int, LiveQuoteResponse | dict]:
                async with group_semaphore:
                    async with self.batch_task_semaphore:
                        try:
                            result = await self.quote(req, on_progress)
                            return index, result
                        except Exception as e:
                            return index, {"hotel": req.hotel_name, "error": str(e)}

            tasks = [crawl_one(req, i) for i, req in items]
            return await asyncio.gather(*tasks)

        regular_items, google_items = self._partition_batch_requests(request.hotels)
        outcomes = await crawl_group(regular_items, concurrency=hotel_concurrency)
        outcomes.extend(await crawl_group(google_items, concurrency=1))

        # Reassemble results in original request order
        outcomes_dict = {idx: outcome for idx, outcome in outcomes}

        for i in range(len(request.hotels)):
            outcome = outcomes_dict[i]
            if isinstance(outcome, LiveQuoteResponse):
                results.append(outcome)
            else:
                errors.append(outcome)

        return BatchQuoteResponse(
            total_hotels=len(request.hotels),
            successful=len(results),
            failed=len(errors),
            results=results,
            errors=errors,
        )

    async def _quote_one_platform(
        self,
        *,
        platform: Platform,
        request: LiveQuoteRequest,
        on_progress: Callable[[str, str, dict[str, Any]], Awaitable[None]] | None = None,
    ) -> LivePlatformResult:
        platform_key = platform.value
        t0 = time.monotonic()
        if on_progress:
            await on_progress(platform_key, "start", {"ts": t0})

        collector = self.collectors[platform]
        known_url = self._resolve_known_url(platform=platform, request=request)

        urls = await self.discovery.discover(
            platform=platform,
            hotel_name=request.hotel_name,
            city=request.city,
            checkin=request.checkin.isoformat(),
            checkout=request.checkout.isoformat(),
            adults=request.adults,
            children=request.children,
            currency=request.currency,
            known_url=known_url,
            locale=request.locale,
            market=request.market,
        )

        if not urls and known_url:
            urls = [known_url]

        if not urls:
            fallback_url = self._fallback_url(platform=platform, request=request)
            if fallback_url:
                urls = [fallback_url]
        urls = self._select_candidate_urls(platform=platform, urls=urls)

        attempts: list[AttemptState] = []
        best: AttemptState | None = None

        offsets = self._build_probe_offsets(
            platform=platform,
            probe_days_ahead=request.probe_days_ahead,
            probe_step_days=request.probe_step_days,
        )

        nights = (request.checkout - request.checkin).days
        max_attempts = 8
        attempt_plan = self._build_attempt_plan(
            urls=urls,
            offsets=offsets,
            max_attempts=max_attempts,
        )

        for candidate_url, offset in attempt_plan:
            checkin = request.checkin + timedelta(days=offset)
            checkout = checkin + timedelta(days=nights)
            hotel_id = self._platform_hotel_id(platform=platform, url=candidate_url, hotel_name=request.hotel_name)

            search = SearchRequest(
                platform_hotel_id=hotel_id,
                platform_url=candidate_url,
                checkin=checkin,
                checkout=checkout,
                adults=request.adults,
                children=request.children,
                currency=request.currency,
                locale=request.locale,
                market=request.market,
            )

            if on_progress:
                await on_progress(
                    platform_key,
                    "attempt",
                    {
                        "attempt_index": len(attempts),
                        "url": candidate_url,
                        "checkin": checkin.isoformat(),
                        "checkout": checkout.isoformat(),
                    },
                )

            try:
                async with self.platform_semaphore:
                    stagger_ms = self.settings.platform_request_stagger_ms
                    if stagger_ms > 0 and len(attempts) > 0:
                        await asyncio.sleep(stagger_ms / 1000)
                    offers = await collector.fetch_rates(search)
            except Exception as exc:
                diagnosis = f"crawl error: {exc}"
                state = AttemptState(
                    request=search,
                    url=candidate_url,
                    offers=[],
                    structured_count=0,
                    fallback_count=0,
                    match_score=0,
                    diagnosis=diagnosis,
                )
                attempts.append(state)
                if on_progress:
                    await on_progress(
                        platform_key,
                        "attempt_done",
                        {
                            "error": str(exc),
                            "offer_count": 0,
                            "structured_count": 0,
                            "diagnosis": diagnosis,
                            "elapsed": time.monotonic() - t0,
                        },
                    )
                continue

            if not offers and platform in {Platform.TRIP, Platform.BOOKING, Platform.AGODA}:
                try:
                    async with self.platform_semaphore:
                        await asyncio.sleep(1.1)
                        offers = await collector.fetch_rates(search)
                except Exception:
                    pass

            structured_count = collector.count_structured_offers(offers)
            fallback_count = self._fallback_count(offers)
            match_score = self._offer_match_score(hotel_name=request.hotel_name, offers=offers)
            diagnosis = self._diagnosis(platform=platform, offers=offers, structured_count=structured_count)

            state = AttemptState(
                request=search,
                url=candidate_url,
                offers=offers,
                structured_count=structured_count,
                fallback_count=fallback_count,
                match_score=match_score,
                diagnosis=diagnosis,
            )
            attempts.append(state)

            if on_progress:
                await on_progress(
                    platform_key,
                    "attempt_done",
                    {
                        "offer_count": len(offers),
                        "structured_count": structured_count,
                        "fallback_count": fallback_count,
                        "diagnosis": diagnosis,
                        "elapsed": time.monotonic() - t0,
                    },
                )

            if best is None or self._is_better_attempt(state, best):
                best = state

            if structured_count > 0:
                break

        if (
            best is not None
            and platform != Platform.AGODA
            and self.settings.firecrawl_enabled
            and getattr(self.settings, "firecrawl_fallback_for_live_quote", True)
            and best.structured_count == 0
            and best.url
        ):
            try:
                if on_progress:
                    await on_progress(
                        platform_key,
                        "attempt",
                        {
                            "attempt_index": len(attempts),
                            "url": best.url,
                            "firecrawl_fallback": True,
                        },
                    )
                scrape_result = await self.firecrawl.scrape_url(best.url)
                body_text = (scrape_result.get("markdown") or "").strip()
                if not body_text and scrape_result.get("html"):
                    body_text = _html_to_text(scrape_result["html"])
                if body_text:
                    firecrawl_offers = collector.parse_text_price_fallback_from_text(
                        body_text=body_text,
                        request=best.request,
                        max_offers=8,
                        mode="firecrawl_fallback",
                    )
                    if firecrawl_offers:
                        fc_structured = collector.count_structured_offers(firecrawl_offers)
                        fc_fallback = self._fallback_count(firecrawl_offers)
                        fc_diag = self._diagnosis(
                            platform=platform,
                            offers=firecrawl_offers,
                            structured_count=fc_structured,
                        )
                        should_replace = len(firecrawl_offers) > len(best.offers) or fc_structured > best.structured_count
                        state = AttemptState(
                            request=best.request,
                            url=best.url,
                            offers=firecrawl_offers,
                            structured_count=fc_structured,
                            fallback_count=fc_fallback,
                            match_score=self._offer_match_score(
                                hotel_name=request.hotel_name, offers=firecrawl_offers
                            ),
                            diagnosis=fc_diag,
                        )
                        if should_replace:
                            attempts.append(state)
                            if self._is_better_attempt(state, best):
                                best = state
                            if on_progress:
                                await on_progress(
                                    platform_key,
                                    "attempt_done",
                                    {
                                        "offer_count": len(firecrawl_offers),
                                        "structured_count": fc_structured,
                                        "diagnosis": fc_diag,
                                        "firecrawl_fallback": True,
                                        "elapsed": time.monotonic() - t0,
                                    },
                                )
            except Exception as exc:
                self.logger.debug("firecrawl fallback failed platform=%s url=%s err=%s", platform_key, best.url, exc)

        if best is None:
            attempt_rows = [
                LiveAttempt(
                    url=item.url,
                    checkin=item.request.checkin,
                    checkout=item.request.checkout,
                    offer_count=len(item.offers),
                    structured_offer_count=item.structured_count,
                    modes=sorted({str(o.raw_payload.get("mode", "parsed")) for o in item.offers}),
                    diagnosis=f"{item.diagnosis}; fallback={item.fallback_count}; match={item.match_score}",
                )
                for item in attempts
            ]
            selected_url = known_url
            if selected_url is None and attempts:
                selected_url = attempts[0].url
            diagnosis = "no candidate URL resolved"
            if attempts:
                diagnosis = "all attempts failed (see attempts diagnosis)"
            if on_progress:
                await on_progress(
                    platform_key,
                    "done",
                    {
                        "success": False,
                        "diagnosis": diagnosis,
                        "attempts": len(attempts),
                        "elapsed": time.monotonic() - t0,
                    },
                )
            return LivePlatformResult(
                platform=platform,
                selected_url=selected_url,
                diagnosis=diagnosis,
                attempts=attempt_rows,
            )

        attempt_rows = [
            LiveAttempt(
                url=item.url,
                checkin=item.request.checkin,
                checkout=item.request.checkout,
                offer_count=len(item.offers),
                structured_offer_count=item.structured_count,
                modes=sorted({str(o.raw_payload.get("mode", "parsed")) for o in item.offers}),
                diagnosis=f"{item.diagnosis}; fallback={item.fallback_count}; match={item.match_score}",
            )
            for item in attempts
        ]

        offer_rows = [
            LiveOffer(
                room_name=offer.room_name,
                final_price=offer.final_price,
                base_price=offer.base_price,
                tax_fee=offer.tax_fee,
                currency=offer.currency,
                meal_plan=offer.meal_plan,
                cancellation_policy=offer.cancellation_policy,
                source_url=offer.source_url,
                mode=str(offer.raw_payload.get("mode", "parsed")),
            )
            for offer in best.offers[:10]
            if not (
                platform == Platform.AGODA
                and str(offer.raw_payload.get("mode", "")).lower()
                in {"text_fallback", "firecrawl_fallback", "url_fallback"}
            )
        ]

        if best.offers:
            try:
                self.mapping_cache.set(
                    UrlMappingRecord(
                        platform=platform,
                        hotel_name=request.hotel_name,
                        city=request.city,
                        url=best.url,
                        structured_offer_count=best.structured_count,
                        offer_count=len(best.offers),
                        diagnosis=best.diagnosis,
                    )
                )
            except Exception as exc:
                self.logger.debug("failed to update mapping cache: %s", exc)

        if on_progress:
            await on_progress(
                platform_key,
                "done",
                {
                    "success": True,
                    "offer_count": len(best.offers),
                    "structured_count": best.structured_count,
                    "diagnosis": best.diagnosis,
                    "elapsed": time.monotonic() - t0,
                },
            )
        return LivePlatformResult(
            platform=platform,
            selected_url=best.url,
            selected_checkin=best.request.checkin,
            selected_checkout=best.request.checkout,
            offer_count=len(best.offers),
            structured_offer_count=best.structured_count,
            diagnosis=best.diagnosis,
            offers=offer_rows,
            attempts=attempt_rows,
        )

    def _resolve_known_url(self, *, platform: Platform, request: LiveQuoteRequest) -> str | None:
        known_urls = request.known_urls
        if not known_urls:
            cached = self.mapping_cache.get(
                platform=platform,
                hotel_name=request.hotel_name,
                city=request.city,
            )
            if cached:
                return cached
            return None
        variants = {
            platform.value,
            platform.name.lower(),
            platform.name,
        }
        for key, value in known_urls.items():
            if key in variants and value:
                return value
        cached = self.mapping_cache.get(
            platform=platform,
            hotel_name=request.hotel_name,
            city=request.city,
        )
        if cached:
            return cached
        return None

    def _fallback_url(self, *, platform: Platform, request: LiveQuoteRequest) -> str | None:
        if platform == Platform.GOOGLE_HOTELS:
            query = f"{request.hotel_name} {request.city}".strip()
            return (
                "https://www.google.com/travel/search"
                f"?q={query.replace(' ', '+')}"
                f"&checkin={request.checkin.isoformat()}"
                f"&checkout={request.checkout.isoformat()}"
                f"&adults={request.adults}&children={request.children}"
            )
        return None

    def _select_candidate_urls(self, *, platform: Platform, urls: list[str]) -> list[str]:
        if not urls:
            return []

        unique: list[str] = []
        for item in urls:
            if item and item not in unique:
                unique.append(item)

        if platform == Platform.GOOGLE_HOTELS:
            return unique[:1]

        detail_urls = [item for item in unique if self.discovery.is_detail_url(platform, item)]
        fallback_urls = [item for item in unique if item not in detail_urls]

        if platform == Platform.TRIP:
            selected = detail_urls[:1] + fallback_urls[:2]
        elif platform in {Platform.BOOKING, Platform.AGODA}:
            selected = detail_urls[:2] + fallback_urls[:1]
        else:
            selected = unique[:3]

        out: list[str] = []
        for item in selected:
            if item not in out:
                out.append(item)
        return out

    def _platform_hotel_id(self, *, platform: Platform, url: str, hotel_name: str) -> str:
        from urllib.parse import parse_qsl, urlparse

        parsed = urlparse(url)
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))

        if platform == Platform.TRIP:
            hotel_id = params.get("hotelId")
            if hotel_id:
                return hotel_id
            match = re.search(r"-hotel-detail-(\d+)", parsed.path.lower())
            if match:
                return match.group(1)
            return hotel_name

        if platform == Platform.BOOKING:
            path = parsed.path.strip("/")
            if path.startswith("hotel/"):
                path = path[len("hotel/") :]
            if path.endswith(".html"):
                path = path[:-5]
            return path or hotel_name

        if platform == Platform.AGODA:
            path = parsed.path.strip("/")
            if "/hotel/" in path:
                return path.split("/hotel/")[0]
            return path or hotel_name

        return hotel_name

    def _is_better_attempt(self, candidate: AttemptState, incumbent: AttemptState) -> bool:
        score_candidate = (
            candidate.structured_count,
            len(candidate.offers),
            candidate.match_score,
            -candidate.fallback_count,
        )
        score_incumbent = (
            incumbent.structured_count,
            len(incumbent.offers),
            incumbent.match_score,
            -incumbent.fallback_count,
        )
        return score_candidate > score_incumbent

    def _fallback_count(self, offers: list) -> int:
        count = 0
        for offer in offers:
            mode = str(offer.raw_payload.get("mode", "")).lower()
            if mode in {"text_fallback", "url_fallback", "demo", "firecrawl_fallback"}:
                count += 1
        return count

    def _diagnosis(self, *, platform: Platform, offers: list, structured_count: int) -> str:
        if structured_count > 0:
            return "structured offers captured"
        if not offers:
            if platform == Platform.TRIP:
                return "no offers; trip anti-bot or API blocked"
            return "no offers captured"

        modes = sorted({str(item.raw_payload.get("mode", "parsed")) for item in offers})
        return f"fallback only ({', '.join(modes)})"

    def _offer_match_score(self, *, hotel_name: str, offers: list) -> int:
        target = {token for token in re.findall(r"[a-z0-9]+", hotel_name.lower()) if len(token) >= 3}
        if not target:
            return 0

        best = 0
        for offer in offers[:8]:
            room = str(getattr(offer, "room_name", "")).lower()
            tokens = {token for token in re.findall(r"[a-z0-9]+", room) if len(token) >= 3}
            if not tokens:
                continue
            overlap = len(target & tokens)
            best = max(best, overlap)
        return best

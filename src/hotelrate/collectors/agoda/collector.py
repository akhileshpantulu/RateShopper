from __future__ import annotations

import re
from decimal import Decimal

from playwright.async_api import async_playwright

from hotelrate.collectors.base import BaseCollector
from hotelrate.core.market_locale import agoda_locale_path_prefix, agoda_should_prepend_locale_prefix
from hotelrate.schemas import Platform, RawRateOffer, SearchRequest


class AgodaCollector(BaseCollector):
    platform = Platform.AGODA
    RESPONSE_PATTERNS = (
        "agoda.com",
        "api",
        "room",
        "offer",
        "price",
        "availability",
    )
    DROPPED_QUERY_KEYS = (
        "searchrequestid",
        "cid",
        "tag",
        "finalPriceView",
        "utm_source",
        "utm_medium",
        "utm_campaign",
    )

    async def fetch_rates(self, request: SearchRequest) -> list[RawRateOffer]:
        if self.settings.demo_mode:
            return [self.build_demo_offer(request, low=70, high=280)]
        offers = await self._fetch_live_rates(request)
        return [
            offer
            for offer in offers
            if str(offer.raw_payload.get("mode", "")).lower()
            not in {"text_fallback", "firecrawl_fallback", "url_fallback"}
        ]

    async def _fetch_live_rates(self, request: SearchRequest) -> list[RawRateOffer]:
        url = self._build_search_url(request)
        async with async_playwright() as pw:
            browser, context = await self.open_playwright_context(pw, request)
            try:
                page = await context.new_page()
                payloads = await self.capture_json_payloads(
                    page=page,
                    url=url,
                    response_patterns=self.RESPONSE_PATTERNS,
                    settle_ms=3000,
                    after_load=self._after_load_interactions,
                )

                # Debug: save raw API responses if enabled
                if getattr(self.settings, "agoda_debug_save_json", False) and payloads:
                    self._save_debug_payloads("agoda", payloads)

                sold_out_detected = self._has_sold_out_signal(payloads)
                offers = self._parse_structured_offers(request=request, payloads=payloads)
                if not offers:
                    offers = await self.parse_ld_json_fallback(page=page, request=request, max_offers=10)
                if not offers:
                    offers = self._parse_below_fold_payloads(request=request, payloads=payloads)
                if not offers and not sold_out_detected:
                    offers = await self._parse_html_prices(page=page, request=request)
                if not offers and not sold_out_detected:
                    offers = await self.parse_text_price_fallback(page=page, request=request, max_offers=6)

                self.logger.info(
                    "agoda crawl url=%s payloads=%d offers=%d",
                    url,
                    len(payloads),
                    len(offers),
                )
            finally:
                await self.close_playwright_context(browser=browser, context=context)
        return offers

    def _has_sold_out_signal(self, payloads: list[dict]) -> bool:
        room_grid_sold_out = False
        below_fold_sold_out = False

        for payload in payloads:
            url = str(payload.get("url", ""))
            data = payload.get("data")
            if not isinstance(data, dict):
                continue

            if "/api/v1/property/room-grid" in url:
                rooms = data.get("rooms")
                is_sold_out = data.get("isSoldOut") is True
                if is_sold_out and isinstance(rooms, list) and len(rooms) == 0:
                    room_grid_sold_out = True

            if "BelowFoldParams/GetSecondaryData" in url:
                cheapest = self._to_decimal(data.get("inquiryProperty", {}).get("cheapestPrice"))
                if cheapest is None or cheapest <= Decimal("0"):
                    sticky = data.get("stickyFooter", {}).get("discount", {}).get("cheapestPriceWithCurrency")
                    sticky_num = self._extract_price_from_text(str(sticky or ""))
                    if sticky_num is None or sticky_num <= Decimal("0"):
                        below_fold_sold_out = True

        return room_grid_sold_out or below_fold_sold_out

    def _extract_price_from_text(self, value: str) -> Decimal | None:
        if not value:
            return None
        match = re.search(r"(\d+(?:[\.,]\d+)?)", value.replace(",", ""))
        if not match:
            return None
        return self._to_decimal(match.group(1))

    def _build_search_url(self, request: SearchRequest) -> str:
        query = {
            "adults": request.adults,
            "children": request.children,
            "rooms": 1,
            "checkIn": request.checkin.isoformat(),
            "los": (request.checkout - request.checkin).days,
            "currencyCode": request.currency,
            "finalPriceView": 1,
        }

        if request.platform_url:
            cleaned = self.remove_url_query_keys(request.platform_url, self.DROPPED_QUERY_KEYS)
            return self.merge_url_query(cleaned, query)

        path = request.platform_hotel_id.strip("/")
        if "/hotel/" in path or path.endswith(".html"):
            rel = path
        else:
            rel = f"{path}/hotel.html"
        prefix = agoda_locale_path_prefix(request.locale)
        if agoda_should_prepend_locale_prefix(rel):
            base_url = f"https://www.agoda.com/{prefix}/{rel}"
        else:
            base_url = f"https://www.agoda.com/{rel}"

        return self.merge_url_query(base_url, query)

    def _parse_structured_offers(
        self,
        *,
        request: SearchRequest,
        payloads: list[dict],
    ) -> list[RawRateOffer]:
        room_grid_offers = self._parse_room_grid_payloads(request=request, payloads=payloads)
        if room_grid_offers:
            return room_grid_offers

        below_fold_offers = self._parse_below_fold_payloads(request=request, payloads=payloads)
        if below_fold_offers:
            return below_fold_offers

        return self.parse_payloads_to_offers(
            request=request,
            payloads=payloads,
            room_keys=("roomName", "masterRoomTypeName", "masterRoomName", "roomTypeName"),
            final_price_keys=("finalPrice", "inclusivePrice", "totalPrice"),
            price_keys=("displayPrice", "basePrice", "exclusivePrice", "price"),
            tax_keys=("taxAndServiceFee", "taxesAndFees", "tax"),
            meal_keys=("mealPlanName", "mealPlan", "boardType"),
            cancellation_keys=("cancellationPolicyText", "cancellationPolicy"),
            currency_keys=("currency", "currencyCode"),
            max_offers=25,
        )

    def _parse_room_grid_payloads(
        self,
        *,
        request: SearchRequest,
        payloads: list[dict],
    ) -> list[RawRateOffer]:
        offers: list[RawRateOffer] = []
        seen: set[tuple[str, str, str]] = set()
        for payload in payloads:
            if "/api/v1/property/room-grid" not in str(payload.get("url", "")):
                continue
            data = payload.get("data")
            if not isinstance(data, dict):
                continue
            rooms = data.get("rooms")
            if not isinstance(rooms, list):
                continue

            for room in rooms:
                if not isinstance(room, dict):
                    continue
                room_name = (
                    str(room.get("masterRoomTypeName", "")).strip()
                    or str(room.get("roomName", "")).strip()
                    or str(room.get("name", "")).strip()
                )
                if not room_name:
                    continue

                room_offers = room.get("offers")
                if isinstance(room_offers, list) and room_offers:
                    for offer in room_offers:
                        if not isinstance(offer, dict):
                            continue
                        price_block = offer.get("price")
                        if not isinstance(price_block, dict):
                            continue

                        final_price = self._extract_decimal_path(price_block, ("final", "amount"))
                        base_price = self._extract_decimal_path(price_block, ("sell", "amount"))
                        if base_price is None:
                            base_price = final_price
                        tax_fee = Decimal("0")
                        if final_price is None and base_price is None:
                            continue
                        if final_price is None and base_price is not None:
                            final_price = base_price
                        if base_price is None and final_price is not None:
                            base_price = final_price
                        if final_price is None or base_price is None or final_price <= Decimal("0"):
                            continue

                        currency = str(
                            self._extract_text(price_block.get("final", {}), ("currency",))
                            or self._extract_text(price_block.get("sell", {}), ("currency",))
                            or request.currency
                        ).upper()
                        cancellation_policy = self._extract_text(
                            offer,
                            ("cancellationPolicyText", "cancellationPolicy", "cancellationType"),
                        )
                        dedupe = (room_name.strip().lower(), f"{final_price:.2f}", currency)
                        if dedupe in seen:
                            continue
                        seen.add(dedupe)
                        offers.append(
                            RawRateOffer(
                                platform=self.platform,
                                platform_hotel_id=request.platform_hotel_id,
                                room_name=room_name[:255],
                                meal_plan=None,
                                cancellation_policy=cancellation_policy[:255] if cancellation_policy else None,
                                currency=currency,
                                base_price=base_price.quantize(Decimal("0.01")),
                                tax_fee=tax_fee,
                                final_price=final_price.quantize(Decimal("0.01")),
                                checkin=request.checkin,
                                checkout=request.checkout,
                                adults=request.adults,
                                children=request.children,
                                source_url=str(payload.get("url", "")) or request.platform_url,
                                raw_payload={
                                    "mode": "room_grid_offer",
                                    "urgency_message": self._extract_text(
                                        offer,
                                        ("urgencyMessage",),
                                    )
                                    or self._extract_text(offer.get("urgencyMessage", {}), ("text",)),
                                },
                            )
                        )
                        if len(offers) >= 25:
                            return offers
                    continue

                final_price = self._extract_decimal_deep(room, ("finalPrice", "inclusivePrice", "totalPrice"), depth=4)
                base_price = self._extract_decimal_deep(room, ("displayPrice", "exclusivePrice", "basePrice"), depth=4)
                tax_fee = self._extract_decimal_deep(room, ("taxesAndFeesAmount", "taxAndServiceFee", "tax", "taxesAndFees"), depth=4) or Decimal("0")

                # Extract cancellation policy
                cancellation_policy = None
                raw_cancel = room.get("cancellationPolicy") or room.get("cancellationType") or room.get("cancellation")
                if raw_cancel:
                    cancellation_policy = str(raw_cancel)[:255]

                if final_price is None and base_price is None:
                    continue
                if final_price is None and base_price is not None:
                    final_price = base_price + tax_fee
                if base_price is None and final_price is not None:
                    base_price = max(final_price - tax_fee, Decimal("0"))
                if final_price is None or base_price is None:
                    continue
                if final_price <= Decimal("0"):
                    continue

                currency = str(room.get("currencyCode") or room.get("currency") or request.currency).upper()
                dedupe = (room_name.strip().lower(), f"{final_price:.2f}", currency)
                if dedupe in seen:
                    continue
                seen.add(dedupe)
                offers.append(
                    RawRateOffer(
                        platform=self.platform,
                        platform_hotel_id=request.platform_hotel_id,
                        room_name=room_name[:255],
                        meal_plan=None,
                        cancellation_policy=cancellation_policy,
                        currency=currency,
                        base_price=base_price.quantize(Decimal("0.01")),
                        tax_fee=tax_fee.quantize(Decimal("0.01")),
                        final_price=final_price.quantize(Decimal("0.01")),
                        checkin=request.checkin,
                        checkout=request.checkout,
                        adults=request.adults,
                        children=request.children,
                        source_url=str(payload.get("url", "")) or request.platform_url,
                        raw_payload={"mode": "room_grid"},
                    )
                )
                if len(offers) >= 25:
                    return offers
        return offers


    def _parse_below_fold_payloads(
        self,
        *,
        request: SearchRequest,
        payloads: list[dict],
    ) -> list[RawRateOffer]:
        offers: list[RawRateOffer] = []
        for payload in payloads:
            url = str(payload.get("url", ""))
            if "BelowFoldParams/GetSecondaryData" not in url:
                continue
            data = payload.get("data")
            if not isinstance(data, dict):
                continue

            room_grid = data.get("roomGridData")
            if isinstance(room_grid, dict):
                master_rooms = room_grid.get("masterRooms")
                if isinstance(master_rooms, list):
                    for room in master_rooms:
                        if not isinstance(room, dict):
                            continue
                        room_name = (
                            str(room.get("masterRoomTypeName", "")).strip()
                            or str(room.get("roomName", "")).strip()
                            or str(room.get("name", "")).strip()
                        )
                        if not room_name:
                            continue

                        final_price = self._extract_decimal_deep(
                            room,
                            ("finalPrice", "inclusivePrice", "totalPrice", "displayPrice", "price"),
                            depth=5,
                        )
                        if final_price is None or final_price <= Decimal("0"):
                            continue

                        # Extract tax fee from API response
                        tax_fee = self._extract_decimal_deep(
                            room,
                            ("taxesAndFeesAmount", "taxAndServiceFee", "tax"),
                            depth=3,
                        ) or Decimal("0")

                        # Extract cancellation policy from API response
                        cancellation_policy = None
                        raw_cancel = room.get("cancellationPolicy") or room.get("cancellationType") or room.get("cancellation")
                        if raw_cancel:
                            cancellation_policy = str(raw_cancel)[:255]

                        currency = str(
                            room.get("currencyCode")
                            or room.get("currency")
                            or data.get("hotelSearchCriteria", {}).get("currencyCode")
                            or request.currency
                        ).upper()
                        amount = final_price.quantize(Decimal("0.01"))
                        offers.append(
                            RawRateOffer(
                                platform=self.platform,
                                platform_hotel_id=request.platform_hotel_id,
                                room_name=room_name[:255],
                                meal_plan=None,
                                cancellation_policy=cancellation_policy,
                                currency=currency,
                                base_price=amount,
                                tax_fee=tax_fee.quantize(Decimal("0.01")),
                                final_price=amount,
                                checkin=request.checkin,
                                checkout=request.checkout,
                                adults=request.adults,
                                children=request.children,
                                source_url=url,
                                raw_payload={"mode": "below_fold_room_grid"},
                            )
                        )
                        if len(offers) >= 20:
                            return offers

            cheapest = self._to_decimal(data.get("inquiryProperty", {}).get("cheapestPrice"))
            if cheapest is None or cheapest <= Decimal("0"):
                sticky = data.get("stickyFooter", {}).get("discount", {}).get("cheapestPriceWithCurrency")
                cheapest = self._to_decimal(sticky)
            if cheapest is None or cheapest <= Decimal("0"):
                continue

            hotel_name = str(data.get("hotelInfo", {}).get("name", "")).strip() or "Agoda offer"
            currency = str(data.get("hotelSearchCriteria", {}).get("currencyCode") or request.currency).upper()
            amount = cheapest.quantize(Decimal("0.01"))
            offers.append(
                RawRateOffer(
                    platform=self.platform,
                    platform_hotel_id=request.platform_hotel_id,
                    room_name=hotel_name[:255],
                    meal_plan=None,
                    cancellation_policy=None,
                    currency=currency,
                    base_price=amount,
                    tax_fee=Decimal("0"),
                    final_price=amount,
                    checkin=request.checkin,
                    checkout=request.checkout,
                    adults=request.adults,
                    children=request.children,
                    source_url=url,
                    raw_payload={"mode": "below_fold_summary"},
                )
            )
        return offers

    def _extract_decimal_path(self, data: dict, keys: tuple[str, ...]) -> Decimal | None:
        current = data
        for key in keys:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        return self._to_decimal(current)

    async def _after_load_interactions(self, page) -> None:
        consent_selectors = [
            "button#onetrust-accept-btn-handler",
            "button:has-text('Accept')",
            "button:has-text('I agree')",
            "button:has-text('同意')",
        ]
        for selector in consent_selectors:
            locator = page.locator(selector).first
            if await locator.count() == 0:
                continue
            try:
                await locator.click(timeout=1200)
                await page.wait_for_timeout(700)
                break
            except Exception:
                continue

        await page.wait_for_timeout(1200)
        await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.55)")
        await page.wait_for_timeout(1200)

        candidates = [
            "button:has-text('Room')",
            "button:has-text('Price')",
            "button:has-text('查看')",
            "button:has-text('客房')",
        ]
        for selector in candidates:
            locator = page.locator(selector).first
            if await locator.count() == 0:
                continue
            try:
                await locator.click(timeout=1200)
                await page.wait_for_timeout(1200)
                break
            except Exception:
                continue

    async def _parse_html_prices(self, page, request: SearchRequest) -> list[RawRateOffer]:
        offers: list[RawRateOffer] = []
        try:
            await page.wait_for_timeout(2500)
            for position in (0.2, 0.55, 0.85):
                await page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {position})")
                await page.wait_for_timeout(700)
        except Exception as exc:
            self.logger.debug("agoda html fallback preparation failed: %s", exc)

        try:
            rows = await page.eval_on_selector_all(
                "[data-fpc-value], [data-element-display-rate], [data-element-hotel-price-per-book]",
                """
                (nodes) => {
                    const out = [];
                    const seen = new Set();
                    const pickPrice = (el) => {
                        for (const attr of ['data-fpc-value', 'data-element-display-rate', 'data-element-hotel-price-per-book']) {
                            const value = el.getAttribute(attr);
                            if (value) return value;
                        }
                        return '';
                    };
                    const findRoomName = (el) => {
                        let current = el;
                        for (let depth = 0; depth < 6 && current; depth += 1) {
                            const selectors = [
                                "[data-selenium='MasterRoom-infoRoomType']",
                                '.MasterRoom-infoRoomType',
                                '[data-element-name]',
                                'h3',
                                'h4'
                            ];
                            for (const selector of selectors) {
                                const target = current.querySelector(selector);
                                const text = (target?.textContent || '').trim();
                                if (text) return text;
                            }
                            current = current.parentElement;
                        }
                        return 'Agoda offer';
                    };

                    for (const el of nodes) {
                        const rawPrice = pickPrice(el);
                        if (!rawPrice) continue;
                        const roomName = findRoomName(el).slice(0, 255);
                        const key = `${roomName}|${rawPrice}`;
                        if (seen.has(key)) continue;
                        seen.add(key);
                        out.push({ roomName, rawPrice });
                        if (out.length >= 12) break;
                    }
                    return out;
                }
                """,
            )
        except Exception as exc:
            self.logger.debug("agoda html fallback extraction failed: %s", exc)
            return []

        seen: set[tuple[str, str]] = set()
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            room_name = str(row.get("roomName", "")).strip() or "Agoda offer"
            price = self._to_decimal(row.get("rawPrice"))
            if price is None or price <= Decimal("0"):
                continue
            amount = price.quantize(Decimal("0.01"))
            dedupe = (room_name.lower(), f"{amount:.2f}")
            if dedupe in seen:
                continue
            seen.add(dedupe)
            offers.append(
                RawRateOffer(
                    platform=self.platform,
                    platform_hotel_id=request.platform_hotel_id,
                    room_name=room_name[:255],
                    meal_plan=None,
                    cancellation_policy=None,
                    currency=request.currency.upper(),
                    base_price=amount,
                    tax_fee=Decimal("0"),
                    final_price=amount,
                    checkin=request.checkin,
                    checkout=request.checkout,
                    adults=request.adults,
                    children=request.children,
                    source_url=request.platform_url or "",
                    raw_payload={"mode": "html_parsing"},
                )
            )
            if len(offers) >= 8:
                break
        return offers

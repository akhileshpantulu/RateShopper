from __future__ import annotations

import re
from decimal import Decimal
from typing import Any
from urllib.parse import parse_qs, unquote_plus, urlparse

from playwright.async_api import async_playwright

from hotelrate.collectors.base import BaseCollector
from hotelrate.core.market_locale import trip_locale_query_value
from hotelrate.schemas import Platform, RawRateOffer, SearchRequest


class TripCollector(BaseCollector):
    platform = Platform.TRIP
    RESPONSE_PATTERNS = (
        "trip.com",
        "hotel",
        "room",
        "price",
        "rateplan",
        "availability",
    )
    DROPPED_QUERY_KEYS = (
        "masterhotelid_tracelogid",
        "subStamp",
        "locale",
    )

    async def fetch_rates(self, request: SearchRequest) -> list[RawRateOffer]:
        if self.settings.demo_mode:
            return [self.build_demo_offer(request, low=80, high=260)]
        return await self._fetch_live_rates(request)

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
                    settle_ms=4200,
                    after_load=self._after_load_interactions,
                )
                signin_redirected = self._is_signin_redirect(page.url)
                offers = self._parse_structured_offers(request=request, payloads=payloads)
                if not offers:
                    offers = await self.parse_ld_json_fallback(page=page, request=request, max_offers=10)
                if not offers and not signin_redirected and await self._has_offer_signals(page=page, request=request):
                    offers = await self.parse_text_price_fallback(page=page, request=request, max_offers=6)

                spider_error = self._find_spider_error_code(payloads)
                if spider_error is not None:
                    self.logger.warning("trip room api returned spider error code=%s", spider_error)
                if signin_redirected:
                    if self.settings.playwright_user_data_dir.strip():
                        self.logger.warning(
                            "trip redirected to sign-in even with persistent profile; refresh local session manually"
                        )
                    else:
                        self.logger.warning(
                            "trip redirected to sign-in; set PLAYWRIGHT_USER_DATA_DIR and run trip login warmup script"
                        )
                self.logger.info(
                    "trip crawl url=%s payloads=%d offers=%d",
                    url,
                    len(payloads),
                    len(offers),
                )
            finally:
                await self.close_playwright_context(browser=browser, context=context)
        return offers

    def _build_search_url(self, request: SearchRequest) -> str:
        query = {
            "hotelId": request.platform_hotel_id,
            "checkIn": request.checkin.isoformat(),
            "checkOut": request.checkout.isoformat(),
            "adult": request.adults,
            "children": request.children,
            "crn": 1,
            "curr": request.currency,
            "barcurr": request.currency,
            "locale": trip_locale_query_value(request.locale),
        }
        if request.platform_url:
            cleaned = self.remove_url_query_keys(request.platform_url, self.DROPPED_QUERY_KEYS)
            return self.merge_url_query(cleaned, query)
        return self.merge_url_query("https://www.trip.com/hotels/detail/", query)

    def _parse_structured_offers(
        self,
        *,
        request: SearchRequest,
        payloads: list[dict],
    ) -> list[RawRateOffer]:
        room_list_offers = self._parse_room_list_oversea_payloads(request=request, payloads=payloads)
        if room_list_offers:
            return room_list_offers

        return self.parse_payloads_to_offers(
            request=request,
            payloads=payloads,
            room_keys=("roomName", "roomTypeName", "roomType", "displayRoomName"),
            final_price_keys=("totalPrice", "finalPrice", "priceAfterTax"),
            price_keys=("price", "displayPrice", "basePrice"),
            tax_keys=("taxFee", "taxesAndFees", "tax"),
            meal_keys=("mealPlan", "breakfast", "boardType"),
            cancellation_keys=("cancellationPolicy", "cancelPolicy"),
            currency_keys=("currency", "currencyCode"),
            max_offers=25,
        )

    def _parse_room_list_oversea_payloads(
        self,
        *,
        request: SearchRequest,
        payloads: list[dict],
    ) -> list[RawRateOffer]:
        offers: list[RawRateOffer] = []
        seen: set[tuple[str, str, str]] = set()
        debug_saved = False

        for payload in payloads:
            url = str(payload.get("url", ""))
            if "gethotelroomlistoversea" not in url.lower():
                continue
            data = payload.get("data")
            if not isinstance(data, dict):
                continue

            if self.settings.trip_debug_save_json and not debug_saved:
                self._save_debug_payloads("trip", payloads)
                debug_saved = True
            body = data.get("data")
            if not isinstance(body, dict):
                continue

            physical_map = body.get("physicRoomMap")
            sale_map = body.get("saleRoomMap")
            if not isinstance(physical_map, dict) or not isinstance(sale_map, dict):
                continue

            for sale_key, raw_sale in sale_map.items():
                if not isinstance(raw_sale, dict):
                    continue

                room_name = self._trip_room_name(raw_sale=raw_sale, physical_map=physical_map)
                if not room_name:
                    continue

                final_price = (
                    self._extract_decimal_path(raw_sale, ("totalPriceInfo", "total", "content"))
                    or self._extract_decimal_path(raw_sale, ("priceDetail", "priceInfo", "totalPrice", "content"))
                    or self._extract_decimal_path(raw_sale, ("priceInfo", "displayPrice"))
                )
                # quantityDays = number of nights, NOT price — do not use for base_price
                base_price = (
                    self._extract_decimal_path(raw_sale, ("priceInfo", "price"))
                    or self._extract_decimal_path(raw_sale, ("priceInfo", "displayPrice"))
                    or self._extract_decimal_path(raw_sale, ("priceDetail", "priceInfo", "totalPrice", "content"))
                )
                tax_fee = (
                    self._extract_decimal_path(raw_sale, ("totalPriceInfo", "payTax", "price"))
                    or self._extract_decimal_path(raw_sale, ("totalPriceInfo", "payTax", "content"))
                    or Decimal("0")
                )

                if final_price is None and base_price is None:
                    continue
                if final_price is None and base_price is not None:
                    final_price = base_price + tax_fee
                if base_price is None and final_price is not None:
                    base_price = max(final_price - tax_fee, Decimal("0"))
                if final_price is None or base_price is None or final_price <= Decimal("0"):
                    continue

                currency = str(
                    self._extract_text(raw_sale.get("priceInfo", {}), ("currency",))
                    or request.currency
                ).upper()
                meal_plan = self._trip_meal_label(raw_sale.get("mealInfo"))
                cancellation_policy = self._trip_cancel_label(raw_sale.get("cancelInfo"))

                dedupe = (room_name.strip().lower(), f"{final_price:.2f}", cancellation_policy or "")
                if dedupe in seen:
                    continue
                seen.add(dedupe)

                offers.append(
                    RawRateOffer(
                        platform=self.platform,
                        platform_hotel_id=request.platform_hotel_id,
                        room_name=room_name[:255],
                        meal_plan=meal_plan[:255] if meal_plan else None,
                        cancellation_policy=cancellation_policy[:1000] if cancellation_policy else None,
                        currency=currency,
                        base_price=base_price.quantize(Decimal("0.01")),
                        tax_fee=tax_fee.quantize(Decimal("0.01")),
                        final_price=final_price.quantize(Decimal("0.01")),
                        checkin=request.checkin,
                        checkout=request.checkout,
                        adults=request.adults,
                        children=request.children,
                        source_url=url or request.platform_url,
                        raw_payload={
                            "mode": "trip_room_list_oversea",
                            "sale_key": str(sale_key),
                        },
                    )
                )

        # Sort by final_price ascending so cheapest rooms appear first
        if offers:
            offers.sort(key=lambda o: o.final_price)
        return offers


    def _trip_room_name(self, *, raw_sale: dict[str, Any], physical_map: dict[str, Any]) -> str:
        direct = str(raw_sale.get("name", "")).strip()
        if direct:
            return direct

        physical_id = raw_sale.get("physicalRoomId")
        if physical_id is not None:
            key = str(physical_id)
            physical = physical_map.get(key)
            if isinstance(physical, dict):
                mapped = str(physical.get("name", "")).strip()
                if mapped:
                    return mapped
        return ""

    def _trip_meal_label(self, meal_info: Any) -> str | None:
        if not isinstance(meal_info, dict):
            return None
        title = str(meal_info.get("title", "")).strip()
        if title:
            return title
        hover = meal_info.get("hover")
        if isinstance(hover, list):
            for item in hover:
                text = str(item).strip()
                if text:
                    return text
        return None

    def _trip_cancel_label(self, cancel_info: Any) -> str | None:
        if not isinstance(cancel_info, dict):
            return None
        for key in ("simpleDesc", "title", "hover"):
            value = cancel_info.get(key)
            if isinstance(value, str):
                text = value.strip()
                if text:
                    return text
        desc_list = cancel_info.get("simpleDescList")
        if isinstance(desc_list, list):
            for item in desc_list:
                if not isinstance(item, dict):
                    continue
                text = str(item.get("txt", "")).strip()
                if text:
                    return text
        return None

    def _extract_decimal_path(self, data: dict[str, Any], keys: tuple[str, ...]) -> Decimal | None:
        current: Any = data
        for key in keys:
            if not isinstance(current, dict):
                return None
            current = current.get(key)
        return self._to_decimal(current)

    def _find_spider_error_code(self, payloads: list[dict]) -> int | None:
        for payload in payloads:
            data = payload.get("data")
            if not isinstance(data, dict):
                continue
            body = data.get("data")
            if not isinstance(body, dict):
                continue
            code = body.get("htlSpiderActionErrorCode")
            if isinstance(code, int):
                return code
        return None

    def _is_signin_redirect(self, page_url: str) -> bool:
        lower = page_url.lower()
        return "/account/signin" in lower or "/login" in lower

    async def _after_load_interactions(self, page) -> None:
        await page.wait_for_timeout(1000)
        try:
            await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.5)")
            await page.wait_for_timeout(900)
        except Exception:
            pass

        selectors = [
            "button:has-text('Room')",
            "button:has-text('Select room')",
            "a:has-text('Room')",
            "button:has-text('客房')",
            "a:has-text('客房')",
        ]
        for selector in selectors:
            locator = page.locator(selector).first
            if await locator.count() == 0:
                continue
            try:
                await locator.click(timeout=1200)
                await page.wait_for_timeout(700)
                break
            except Exception:
                continue

    async def _has_offer_signals(self, *, page, request: SearchRequest) -> bool:
        try:
            page_data = await page.evaluate(
                """
                () => {
                  const bodyText = (document.body?.innerText || '').toLowerCase();
                  const hrefs = Array.from(document.querySelectorAll('a[href]'))
                    .slice(0, 400)
                    .map((a) => String(a.getAttribute('href') || '').toLowerCase());
                  const hasDetailLink = hrefs.some(
                    (h) => h.includes('hotel-detail-') || h.includes('/hotels/detail/')
                  );
                  return {
                    bodyText,
                    hasDetailLink,
                  };
                }
                """
            )
        except Exception:
            return False

        if not isinstance(page_data, dict):
            return False

        body_text = str(page_data.get("bodyText", ""))
        has_detail_link = bool(page_data.get("hasDetailLink"))
        if has_detail_link:
            return True

        tokens = self._trip_query_tokens(request)
        if not tokens:
            return False

        overlap = sum(1 for token in tokens if token in body_text)
        return overlap >= 2

    def _trip_query_tokens(self, request: SearchRequest) -> set[str]:
        source = request.platform_hotel_id
        if request.platform_url:
            parsed = urlparse(request.platform_url)
            qs = parse_qs(parsed.query)
            keyword = qs.get("keyword", [""])[0]
            if keyword:
                source += " " + unquote_plus(keyword)
        tokens = {item for item in re.findall(r"[a-z0-9]+", source.lower()) if len(item) >= 4}
        stop = {"hotel", "hotels", "trip", "city", "room", "rooms", "adult", "children"}
        return {item for item in tokens if item not in stop}

from __future__ import annotations

import re
from decimal import Decimal
from urllib.parse import parse_qs, parse_qsl, unquote, urlparse

from playwright.async_api import async_playwright

from hotelrate.collectors.base import BaseCollector
from hotelrate.core.market_locale import booking_lang_param
from hotelrate.schemas import Platform, RawRateOffer, SearchRequest


class BookingCollector(BaseCollector):
    platform = Platform.BOOKING
    RESPONSE_PATTERNS = (
        "booking.com",
        "graphql",
        "availability",
        "price",
        "room",
        "block",
    )
    DROPPED_QUERY_KEYS = (
        "sid",
        "srepoch",
        "srpvid",
        "label",
        "utm_source",
        "utm_medium",
        "utm_campaign",
        "from",
        "source",
    )

    async def fetch_rates(self, request: SearchRequest) -> list[RawRateOffer]:
        if self.settings.demo_mode:
            return [self.build_demo_offer(request, low=90, high=310)]
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
                    settle_ms=3000,
                    after_load=self._after_load_interactions,
                )

                # Debug: save raw API responses if enabled
                if getattr(self.settings, "booking_debug_save_json", False) and payloads:
                    self._save_debug_payloads("booking", payloads)

                offers = self._parse_structured_offers(request=request, payloads=payloads)
                if not offers:
                    offers = await self.parse_ld_json_fallback(page=page, request=request, max_offers=10)
                if not offers:
                    offers = await self._parse_booking_env_room_offers(page=page, request=request)
                if not offers:
                    offers = await self._parse_room_table_dom_fallback(page=page, request=request)
                if not offers:
                    offers = self._parse_url_price_fallback(request)
                if not offers:
                    offers = await self.parse_text_price_fallback(page=page, request=request, max_offers=6)
                offers = self._filter_text_fallback_outliers(offers)
                if not offers and "pipl_consent" in str(page.url).lower():
                    raise RuntimeError(
                        "booking consent gate blocked; run scripts/platform_login_warmup.py --platform booking"
                    )
                self.logger.info(
                    "booking crawl url=%s payloads=%d offers=%d",
                    url,
                    len(payloads),
                    len(offers),
                )
            finally:
                await self.close_playwright_context(browser=browser, context=context)
        return offers

    def _filter_text_fallback_outliers(self, offers: list[RawRateOffer]) -> list[RawRateOffer]:
        if not offers:
            return offers
        modes = {str(item.raw_payload.get("mode", "")).lower() for item in offers}
        if modes != {"text_fallback"}:
            return offers

        priced = [item for item in offers if item.final_price > Decimal("0")]
        if len(priced) <= 2:
            return offers

        floor = min(item.final_price for item in priced)
        cap = max(floor * Decimal("2.2"), floor + Decimal("180"))
        kept: list[RawRateOffer] = []
        seen: set[str] = set()
        for item in sorted(priced, key=lambda x: x.final_price):
            if item.final_price > cap:
                continue
            key = f"{item.final_price:.2f}"
            if key in seen:
                continue
            seen.add(key)
            kept.append(item)
            if len(kept) >= 4:
                break
        return kept or offers

    def _build_search_url(self, request: SearchRequest) -> str:
        query = {
            "checkin": request.checkin.isoformat(),
            "checkout": request.checkout.isoformat(),
            "group_adults": request.adults,
            "group_children": request.children,
            "req_adults": request.adults,
            "req_children": request.children,
            "no_rooms": 1,
            "sb_price_type": "total",
            "selected_currency": request.currency,
            "lang": booking_lang_param(request.locale),
        }

        if request.platform_url:
            cleaned = self.remove_url_query_keys(request.platform_url, self.DROPPED_QUERY_KEYS)
            return self.merge_url_query(cleaned, query)

        path = request.platform_hotel_id.strip("/")
        if path.startswith("hotel/"):
            path = path[len("hotel/") :]
        if not path.endswith(".html"):
            path = f"{path}.html"

        base_url = f"https://www.booking.com/hotel/{path}"
        return self.merge_url_query(base_url, query)

    def _parse_structured_offers(
        self,
        *,
        request: SearchRequest,
        payloads: list[dict],
    ) -> list[RawRateOffer]:
        return self.parse_payloads_to_offers(
            request=request,
            payloads=payloads,
            room_keys=("room_name", "roomName", "roomType", "block_name"),
            final_price_keys=("price_after_tax", "priceAfterTax", "totalPrice", "gross_price"),
            price_keys=("price", "displayPrice", "price_without_tax", "base_price"),
            tax_keys=("taxes", "taxesAndFees", "tax"),
            meal_keys=("mealplan", "meal_plan", "board", "breakfast"),
            cancellation_keys=("cancellation", "cancellationPolicy", "free_cancellation"),
            currency_keys=("currency", "currency_code"),
            max_offers=25,
        )

    async def _after_load_interactions(self, page) -> None:
        await self._handle_consent_gate(page)

        selectors = [
            "[data-testid='Property-Header-Nav-Tab-Trigger-availability']",
            "#hp_availability_style_changes",
            "[href='#hp_availability_style_changes']",
        ]

        for selector in selectors:
            locator = page.locator(selector).first
            if await locator.count() == 0:
                continue
            try:
                await locator.click(timeout=1500)
                await page.wait_for_timeout(800)
            except Exception:
                continue

        await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.7)")
        await page.wait_for_timeout(1200)

    async def _handle_consent_gate(self, page) -> None:
        lower_url = str(page.url).lower()
        if "pipl_consent" in lower_url:
            parsed = urlparse(page.url)
            target = parse_qs(parsed.query).get("target_page", [""])[0]
            target = unquote(target)

            for _ in range(2):
                await page.wait_for_timeout(800)
                await self._tick_booking_consent_checkboxes(page)
                await self._submit_booking_consent(page)

                try:
                    await page.wait_for_function(
                        "() => !location.href.toLowerCase().includes('pipl_consent')",
                        timeout=10000,
                    )
                    return
                except Exception:
                    continue

            if "pipl_consent" not in str(page.url).lower():
                return

            if target.startswith("http"):
                try:
                    await page.goto(target, wait_until="domcontentloaded", timeout=60000)
                    await page.wait_for_timeout(1000)
                    if "pipl_consent" not in str(page.url).lower():
                        return
                except Exception:
                    pass

        selectors = [
            "button#onetrust-accept-btn-handler",
            "button[data-testid='consent-banner-accept-all']",
            "button:has-text('Accept')",
            "button:has-text('I agree')",
            "button:has-text('同意')",
        ]
        for selector in selectors:
            locator = page.locator(selector).first
            if await locator.count() == 0:
                continue
            try:
                await locator.click(timeout=1500)
                await page.wait_for_timeout(800)
                break
            except Exception:
                continue

    async def _tick_booking_consent_checkboxes(self, page) -> None:
        label_selectors = [
            "label:has-text('Select all')",
            "label:has-text('全选')",
            "label:has-text('I’m at least 18 years old')",
            "label:has-text('I consent to')",
            "label:has-text('我已年满')",
            "label:has-text('我同意')",
        ]
        for selector in label_selectors:
            locator = page.locator(selector)
            count = await locator.count()
            if count == 0:
                continue
            limit = min(count, 5)
            for idx in range(limit):
                item = locator.nth(idx)
                try:
                    await item.click(timeout=1000)
                    await page.wait_for_timeout(120)
                except Exception:
                    continue

        try:
            await page.evaluate(
                """
                () => {
                  const boxes = Array.from(document.querySelectorAll('input[type="checkbox"]'));
                  for (const box of boxes) {
                    if (box.checked) continue;
                    const id = box.getAttribute('id');
                    if (id) {
                      const label = document.querySelector(`label[for="${id}"]`);
                      if (label) {
                        label.click();
                        continue;
                      }
                    }
                    box.click();
                  }
                }
                """
            )
        except Exception:
            pass

        await page.wait_for_timeout(200)

    async def _submit_booking_consent(self, page) -> None:
        await page.wait_for_timeout(250)
        try:
            role_btn = page.get_by_role(
                "button",
                name=re.compile(r"(agree|i agree|同意|确认|continue|继续)", re.IGNORECASE),
            ).first
            if await role_btn.count() > 0:
                await role_btn.click(timeout=2200)
                await page.wait_for_timeout(500)
                return
        except Exception:
            pass

        agree_selectors = [
            "button:has-text('Agree')",
            "button:has-text('I agree')",
            "button:has-text('同意')",
            "button:has-text('确认')",
            "button:has-text('Continue')",
            "button:has-text('继续')",
        ]
        for selector in agree_selectors:
            locator = page.locator(selector).first
            if await locator.count() == 0:
                continue
            try:
                await locator.click(timeout=1800)
                await page.wait_for_timeout(500)
                return
            except Exception:
                continue

    async def _parse_room_table_dom_fallback(self, *, page, request: SearchRequest) -> list[RawRateOffer]:
        room_rows = await page.evaluate(
            """
            () => {
              const rows = [];
              const tableRows = Array.from(document.querySelectorAll('#maxotelRoomArea tr'));
              const priceRe = /(?:US\\$|\\$|USD)\\s*([0-9][0-9,]*(?:\\.[0-9]{1,2})?)/i;
              for (const row of tableRows) {
                const roomNode = row.querySelector("[data-testid='rt-name-link']");
                const roomName = (roomNode?.textContent || '').trim();
                if (!roomName) continue;
                const rowText = (row.innerText || '').trim();
                const match = rowText.match(priceRe);
                rows.push({
                  roomName,
                  rowText: rowText.slice(0, 400),
                  price: match ? match[1] : '',
                });
              }
              return rows;
            }
            """
        )

        if not room_rows:
            return []

        fallback_prices = self._parse_url_price_candidates(request)
        default_price = min(fallback_prices) if fallback_prices else None
        offers: list[RawRateOffer] = []
        seen: set[tuple[str, str]] = set()

        for row in room_rows[:12]:
            room_name = str(row.get("roomName", "")).strip()
            if not room_name:
                continue

            row_price = self._to_decimal(row.get("price"))
            amount = row_price or default_price
            if amount is None or amount <= Decimal("0"):
                continue

            amount = amount.quantize(Decimal("0.01"))
            dedupe = (room_name.lower(), f"{amount:.2f}")
            if dedupe in seen:
                continue
            seen.add(dedupe)

            mode = "dom_room_table" if row_price is not None else "dom_room_table_url_price"
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
                    source_url=request.platform_url,
                    raw_payload={
                        "mode": mode,
                        "snippet": str(row.get("rowText", ""))[:320],
                    },
                )
            )

            if len(offers) >= 10:
                break
        return offers

    async def _parse_booking_env_room_offers(self, *, page, request: SearchRequest) -> list[RawRateOffer]:
        rows = await page.evaluate(
            """
            () => {
              try {
                if (!window.booking || !booking.env) return [];
                const rooms = booking.env.b_rooms_available_and_soldout;
                if (!Array.isArray(rooms)) return [];
                const out = [];
                for (const room of rooms.slice(0, 16)) {
                  const roomName = (room?.b_name || '').trim();
                  const blocks = Array.isArray(room?.b_blocks) ? room.b_blocks : [];
                  for (const block of blocks.slice(0, 5)) {
                    out.push({
                      roomName,
                      displayPrice: block?.b_price || '',
                      rawPrice: block?.b_raw_price || '',
                      cancellationType: block?.b_cancellation_type || '',
                      mealPlan: block?.b_mealplan_included_name || '',
                      blockId: block?.b_block_id || '',
                    });
                  }
                }
                return out;
              } catch (e) {
                return [];
              }
            }
            """
        )

        if not rows:
            return []

        offers: list[RawRateOffer] = []
        seen: set[tuple[str, str, str]] = set()
        for row in rows:
            room_name = str(row.get("roomName", "")).strip()
            if not room_name:
                continue

            amount = self._to_decimal(row.get("rawPrice")) or self._to_decimal(row.get("displayPrice"))
            if amount is None or amount <= Decimal("0"):
                continue
            amount = amount.quantize(Decimal("0.01"))

            cancellation = self._normalize_booking_cancellation(str(row.get("cancellationType", "")))
            meal = self._normalize_booking_meal(str(row.get("mealPlan", "")))
            dedupe = (room_name.lower(), f"{amount:.2f}", cancellation or "")
            if dedupe in seen:
                continue
            seen.add(dedupe)

            offers.append(
                RawRateOffer(
                    platform=self.platform,
                    platform_hotel_id=request.platform_hotel_id,
                    room_name=room_name[:255],
                    meal_plan=meal,
                    cancellation_policy=cancellation,
                    currency=request.currency.upper(),
                    base_price=amount,
                    tax_fee=Decimal("0"),
                    final_price=amount,
                    checkin=request.checkin,
                    checkout=request.checkout,
                    adults=request.adults,
                    children=request.children,
                    source_url=request.platform_url,
                    raw_payload={
                        "mode": "booking_env",
                        "block_id": str(row.get("blockId", "")),
                    },
                )
            )
            if len(offers) >= 12:
                break
        return offers

    def _normalize_booking_cancellation(self, raw: str) -> str | None:
        value = raw.strip().lower()
        if not value:
            return None
        if "free" in value:
            return "Free cancellation"
        if "non_refundable" in value or "non refundable" in value:
            return "Non-refundable"
        return value.replace("_", " ").title()

    def _normalize_booking_meal(self, raw: str) -> str | None:
        value = raw.strip().lower()
        if not value:
            return None
        if value == "breakfast":
            return "Breakfast included"
        return value.replace("_", " ").title()

    def _parse_url_price_fallback(self, request: SearchRequest) -> list[RawRateOffer]:
        prices = self._parse_url_price_candidates(request)
        if not prices:
            return []

        parsed = urlparse(request.platform_url or "")
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        final_price = min(prices)
        currency = params.get("selected_currency", request.currency).upper()
        return [
            RawRateOffer(
                platform=self.platform,
                platform_hotel_id=request.platform_hotel_id,
                room_name="Selected booking block",
                meal_plan=None,
                cancellation_policy=None,
                currency=currency,
                base_price=final_price,
                tax_fee=Decimal("0"),
                final_price=final_price,
                checkin=request.checkin,
                checkout=request.checkout,
                adults=request.adults,
                children=request.children,
                source_url=request.platform_url,
                raw_payload={"mode": "url_fallback"},
            )
        ]

    def _parse_url_price_candidates(self, request: SearchRequest) -> list[Decimal]:
        if not request.platform_url:
            return []

        parsed = urlparse(request.platform_url)
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        raw_blocks = params.get("sr_pri_blocks", "")
        if not raw_blocks:
            return []

        prices: list[Decimal] = []
        for block in raw_blocks.split(","):
            if "__" not in block:
                continue
            raw = block.rsplit("__", 1)[-1].strip()
            if not raw.isdigit():
                continue
            value = Decimal(raw)
            if value >= Decimal("1000"):
                value = (value / Decimal("100")).quantize(Decimal("0.01"))
            if value > Decimal("0") and value >= Decimal("20"):
                prices.append(value)

        return prices

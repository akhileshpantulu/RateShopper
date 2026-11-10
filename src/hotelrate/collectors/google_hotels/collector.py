from __future__ import annotations

import json
import time
import re
from decimal import Decimal
from pathlib import Path
from typing import Any

from playwright.async_api import async_playwright

from hotelrate.collectors.base import BaseCollector
from hotelrate.core.market_locale import google_hl_gl
from hotelrate.schemas import Platform, RawRateOffer, SearchRequest


class GoogleHotelsCollector(BaseCollector):
    platform = Platform.GOOGLE_HOTELS
    RESPONSE_PATTERNS = (
        "google.com",
        "travel",
        "TravelFrontendUi",
        "HotelRoom",
        "HotelSearch",
        "mercury",
        "batch",
        "hotel",
        "offers",
        "price",
        "availability",
        "search",
    )
    DROPPED_QUERY_KEYS = (
        "ved",
        "ei",
        "ictx",
        "sxsrf",
        "sourceid",
    )
    SUPPLIER_KEYWORDS = (
        "booking",
        "agoda",
        "trip.com",
        "trip",
        "expedia",
        "hotels.com",
        "hotels",
        "klook",
        "priceline",
        "traveluro",
        "travelocity",
        "kayak",
        "wego",
        "official site",
        "official website",
        "官方网站",
    )

    async def fetch_rates(self, request: SearchRequest) -> list[RawRateOffer]:
        if self.settings.demo_mode:
            return [self.build_demo_offer(request, low=85, high=330)]

        if getattr(self.settings, "google_skip", False):
            self.logger.info("Google Hotels skipped per config")
            return []

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
                    settle_ms=4500,
                    after_load=lambda p: self._after_load_interactions(p, request=request),
                )
                offers = self._parse_structured_offers(request=request, payloads=payloads)
                if not offers:
                    offers = await self._parse_script_json_offers(page=page, request=request)
                if not offers:
                    offers = await self.parse_ld_json_fallback(page=page, request=request, max_offers=10)
                if not offers:
                    offers = await self._parse_google_travel_dom_selectors(page=page, request=request)
                if not offers:
                    offers = await self._parse_google_price_panel_dom(page=page, request=request)
                if not offers:
                    offers = await self._parse_supplier_text_offers(page=page, request=request)
                if not offers:
                    offers = await self._parse_aria_label_card_offers(page=page, request=request)
                if not offers:
                    offers = await self._parse_listing_dom_offers(page=page, request=request)
                if not offers:
                    offers = await self.parse_text_price_fallback(page=page, request=request, max_offers=8)
                structured_count = sum(
                    1
                    for o in offers
                    if str(o.raw_payload.get("mode", "")).lower() not in ("text_fallback", "url_fallback", "demo")
                )
                if getattr(self.settings, "google_debug_save_runtime_artifacts", False):
                    await self._save_google_debug_artifacts(
                        page=page,
                        request=request,
                        payloads=payloads,
                        offers=offers,
                    )
                if not offers:
                    text_offers = await self._parse_supplier_text_offers(page=page, request=request)
                    if text_offers:
                        offers = text_offers
                if getattr(self.settings, "google_debug_save_html", False) and structured_count == 0 and offers:
                    try:
                        html = await page.content()
                        reports_dir = Path("reports")
                        reports_dir.mkdir(parents=True, exist_ok=True)
                        path = reports_dir / f"google_debug_{time.strftime('%Y%m%d_%H%M%S', time.localtime())}.html"
                        path.write_text(html, encoding="utf-8")
                        self.logger.info("google debug: saved page HTML to %s", path)
                    except Exception as e:
                        self.logger.debug("google debug save failed: %s", e)
                self.logger.info(
                    "google hotels crawl url=%s payloads=%d offers=%d",
                    url,
                    len(payloads),
                    len(offers),
                )
            finally:
                await self.close_playwright_context(browser=browser, context=context)
        return offers

    async def _save_google_debug_artifacts(
        self,
        *,
        page,
        request: SearchRequest,
        payloads: list[dict[str, Any]],
        offers: list[RawRateOffer],
    ) -> None:
        if not getattr(self.settings, "google_debug_save_runtime_artifacts", False):
            return
        try:
            reports_dir = Path("reports")
            reports_dir.mkdir(parents=True, exist_ok=True)
            stamp = int(time.time() * 1000)
            html_path = reports_dir / f"google_runtime_{stamp}.html"
            json_path = reports_dir / f"google_runtime_{stamp}.json"

            html = await page.content()
            html_path.write_text(html, encoding="utf-8")

            runtime_probe = await page.evaluate(
                """
                () => {
                    const windowKeys = Object.keys(window)
                        .filter((key) => /travel|hotel|price|merchant|provider|offer|google/i.test(key))
                        .slice(0, 200);

                    const cardNodes = Array.from(document.querySelectorAll('.ADs2Tc, .CcERhd')).slice(0, 12);
                    const cardSamples = cardNodes.map((node) => ({
                        text: (node.innerText || '').slice(0, 500),
                        html: (node.outerHTML || '').slice(0, 2500),
                    }));

                    const ariaSamples = Array.from(document.querySelectorAll('a[aria-label], button[aria-label]')).slice(0, 40).map((node) => ({
                        aria: String(node.getAttribute('aria-label') || '').slice(0, 300),
                        text: String(node.innerText || '').slice(0, 300),
                    }));

                    const scripts = Array.from(document.scripts || []).map((script, idx) => {
                        const text = String(script.textContent || '');
                        return {
                            index: idx,
                            hasAfInitData: text.includes('AF_initDataCallback'),
                            hasTravelKeyword: /travel|hotel|price|merchant|provider|offer/i.test(text),
                            snippet: text.slice(0, 2000),
                        };
                    }).filter((item) => item.hasAfInitData || item.hasTravelKeyword).slice(0, 20);

                    return {
                        url: location.href,
                        title: document.title,
                        bodyTextSample: String(document.body?.innerText || '').slice(0, 12000),
                        windowKeys,
                        cardCount: cardNodes.length,
                        cardSamples,
                        ariaSamples,
                        scripts,
                    };
                }
                """
            )

            payload_text_hits = []
            for payload in payloads[:30]:
                try:
                    text = json.dumps(payload.get("data"), ensure_ascii=False)
                except Exception:
                    text = str(payload.get("data"))
                if re.search(
                    r"Booking\.com|Agoda|Trip\.com|Expedia|Hotels\.com|Official website|官方网站|Bluepillow",
                    text,
                    re.IGNORECASE,
                ):
                    payload_text_hits.append(
                        {
                            "url": payload.get("url"),
                            "status": payload.get("status"),
                            "snippet": text[:4000],
                        }
                    )

            debug_doc = {
                "request": request.model_dump(mode="json"),
                "offer_count": len(offers),
                "offer_modes": sorted({str(item.raw_payload.get("mode", "")) for item in offers}),
                "payload_count": len(payloads),
                "payload_urls": [str(item.get("url", "")) for item in payloads[:80]],
                "payload_text_hits": payload_text_hits[:20],
                "runtime_probe": runtime_probe,
            }
            json_path.write_text(json.dumps(debug_doc, ensure_ascii=False, indent=2), encoding="utf-8")
            self.logger.info("google debug: saved runtime artifacts to %s and %s", html_path, json_path)
        except Exception as exc:
            self.logger.debug("google runtime debug save failed: %s", exc)

    def _build_search_url(self, request: SearchRequest) -> str:
        hl, gl = google_hl_gl(request.locale, request.market)
        query = {
            "q": request.platform_hotel_id,
            "checkin": request.checkin.isoformat(),
            "checkout": request.checkout.isoformat(),
            "adults": request.adults,
            "children": request.children,
            "hl": hl,
            "gl": gl,
        }

        if request.platform_url:
            cleaned = self.remove_url_query_keys(request.platform_url, self.DROPPED_QUERY_KEYS)
            return self.merge_url_query(cleaned, query)

        return self.merge_url_query("https://www.google.com/travel/hotels", query)

    def _parse_structured_offers(
        self,
        *,
        request: SearchRequest,
        payloads: list[dict],
    ) -> list[RawRateOffer]:
        return self.parse_payloads_to_offers(
            request=request,
            payloads=payloads,
            room_keys=("roomName", "room_type", "roomType", "offerName", "name"),
            final_price_keys=("totalPrice", "finalPrice", "price", "amount"),
            price_keys=("basePrice", "priceBeforeTax", "price", "amount"),
            tax_keys=("tax", "fees", "taxesAndFees"),
            meal_keys=("mealPlan", "board", "breakfast"),
            cancellation_keys=("cancellationPolicy", "freeCancellation"),
            currency_keys=("currencyCode", "currency"),
            max_offers=25,
        )

    async def _parse_script_json_offers(self, *, page, request: SearchRequest) -> list[RawRateOffer]:
        try:
            raw = await page.evaluate(
                r"""
                () => {
                    const out = [];
                    const seen = new Set();
                    const add = (name, price, mode) => {
                        if (typeof name !== 'string') return;
                        const trimmed = name.trim();
                        if (!trimmed) return;
                        const num = typeof price === 'string' ? parseFloat(price.replace(/,/g, '')) : Number(price);
                        if (!Number.isFinite(num) || num < 20 || num > 10000) return;
                        const key = `${trimmed.slice(0, 120)}|${num.toFixed(2)}|${mode}`;
                        if (seen.has(key)) return;
                        seen.add(key);
                        out.push({ name: trimmed.slice(0, 200), price: num, mode });
                    };

                    const tryParse = (obj, depth, mode) => {
                        if (depth > 8) return;
                        if (!obj || typeof obj !== 'object') return;
                        if (Array.isArray(obj)) {
                            obj.slice(0, 80).forEach((item) => tryParse(item, depth + 1, mode));
                            return;
                        }
                        const keys = Object.keys(obj);
                        const hasPrice = keys.some((k) => /price|amount|total/i.test(k));
                        const hasName = keys.some((k) => /name|room|supplier|provider|merchant|title|hotel/i.test(k));
                        if (hasPrice && hasName) {
                            const name = obj.name || obj.roomName || obj.supplierName || obj.providerName || obj.merchantName || obj.title || obj.hotelName || '';
                            const price = obj.price || obj.totalPrice || obj.finalPrice || obj.amount || obj.displayPrice;
                            add(name, price, mode);
                        }
                        keys.forEach((k) => tryParse(obj[k], depth + 1, mode));
                    };

                    ['__INITIAL_STATE__', '__NEXT_DATA__', '__NUXT__', 'initialState', 'pageData'].forEach((key) => {
                        if (typeof window[key] !== 'undefined') tryParse(window[key], 0, `window:${key}`);
                    });

                    const scripts = Array.from(document.scripts || []);
                    for (const script of scripts) {
                        const text = String(script.textContent || '');
                        if (!text || !text.includes('AF_initDataCallback')) continue;
                        if (/Booking\.com|Agoda|Trip\.com|Expedia|Hotels\.com|Official website|官方网站|Bluepillow/.test(text)) {
                            const priceMatches = [...text.matchAll(/(?:US\$|USD|\$|¥|￥|€|£)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/g)].slice(0, 20);
                            const supplierMatches = [...text.matchAll(/Booking\.com|Agoda|Trip\.com|Expedia|Hotels\.com|KAYAK|Official website|官方网站|Bluepillow(?:\.hk)?/g)].map((m) => m[0]);
                            for (let i = 0; i < Math.min(priceMatches.length, supplierMatches.length, 12); i += 1) {
                                add(supplierMatches[i], priceMatches[i][1], 'af_initdata_text');
                            }
                        }
                    }

                    return out.slice(0, 30);
                }
                """
            )
        except Exception:
            return []
        if not raw or not isinstance(raw, list):
            return []
        offers = []
        seen: set[tuple[str, str]] = set()
        for item in raw:
            if not isinstance(item, dict):
                continue
            name = str(item.get("name", "")).strip()
            price = self._to_decimal(item.get("price"))
            if not name or price is None or price < Decimal("20"):
                continue
            key = (name.lower()[:80], f"{price:.2f}")
            if key in seen:
                continue
            seen.add(key)
            offers.append(
                RawRateOffer(
                    platform=self.platform,
                    platform_hotel_id=request.platform_hotel_id,
                    room_name=name[:255],
                    meal_plan=None,
                    cancellation_policy=None,
                    currency=request.currency.upper(),
                    base_price=price,
                    tax_fee=Decimal("0"),
                    final_price=price,
                    checkin=request.checkin,
                    checkout=request.checkout,
                    adults=request.adults,
                    children=request.children,
                    source_url=request.platform_url,
                    raw_payload={"mode": str(item.get("mode", "script_json"))},
                )
            )
            if len(offers) >= 15:
                break
        return offers

    async def _parse_google_travel_dom_selectors(self, *, page, request: SearchRequest) -> list[RawRateOffer]:
        try:
            try:
                await page.wait_for_selector(".ADs2Tc, .CcERhd", state="attached", timeout=8000)
            except Exception:
                return []

            rows = await page.eval_on_selector_all(
                ".ADs2Tc, .CcERhd",
                r"""
                (cards) => {
                    const out = [];
                    const seen = new Set();
                    const supplierSelectors = [
                        '.RjilDd',
                        '.FjC1We',
                        '.lRagtb',
                        '.sSHqwe',
                        '[data-testid="price-provider"]',
                        '[class*="provider"]',
                        '[class*="merchant"]',
                    ];
                    const priceSelectors = [
                        '.iqYCVb',
                        '.nDkDDb',
                        '.MW1oTb',
                        '.UeIHqb',
                        '[data-testid="price"]',
                        '[aria-label*="$"]',
                        '[class*="price"]',
                    ];
                    const supplierHints = [
                        'Booking.com', 'Agoda', 'Trip.com', 'Expedia', 'Hotels.com', 'KAYAK',
                        'Travelocity', 'Orbitz', 'Priceline', 'Official website', '官方网站', 'Bluepillow', 'Bluepillow.hk'
                    ];
                    const priceRe = /(?:US\$|USD|\$|¥|￥|€|£)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/i;

                    const findText = (root, selectors) => {
                        for (const selector of selectors) {
                            const node = root.querySelector(selector);
                            const text = (node?.textContent || '').trim();
                            if (text) return text;
                        }
                        return '';
                    };

                    for (const card of cards) {
                        const cardText = (card.innerText || '').trim();
                        if (!cardText) continue;

                        let supplier = findText(card, supplierSelectors);
                        if (!supplier) {
                            for (const hint of supplierHints) {
                                if (cardText.includes(hint)) {
                                    supplier = hint;
                                    break;
                                }
                            }
                        }
                        if (!supplier) {
                            const domainMatch = cardText.match(/([a-z0-9.-]+\.(?:com|cn|net|org|hk))/i);
                            if (domainMatch) supplier = domainMatch[1];
                        }
                        if (!supplier) continue;

                        let priceText = findText(card, priceSelectors);
                        if (!priceText) {
                            const lines = cardText.split('\n').map((line) => line.trim()).filter(Boolean);
                            priceText = lines.find((line) => priceRe.test(line)) || '';
                        }
                        const match = priceText.match(priceRe) || cardText.match(priceRe);
                        if (!match) continue;
                        const amount = parseFloat(match[1].replace(/,/g, ''));
                        if (Number.isNaN(amount) || amount < 20 || amount > 10000) continue;

                        const key = `${supplier}|${amount}`;
                        if (seen.has(key)) continue;
                        seen.add(key);
                        out.push({
                            supplier,
                            price: amount,
                            snippet: cardText.slice(0, 240),
                        });
                        if (out.length >= 25) break;
                    }
                    return out;
                }
                """,
            )
        except Exception:
            return []
        if not rows or not isinstance(rows, list):
            return []
        offers = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            supplier = str(row.get("supplier", "")).strip()
            price = self._to_decimal(row.get("price"))
            if not supplier or price is None or price < Decimal("20"):
                continue
            key = (supplier.lower(), f"{price:.2f}")
            if key in seen:
                continue
            seen.add(key)
            offers.append(
                RawRateOffer(
                    platform=self.platform,
                    platform_hotel_id=request.platform_hotel_id,
                    room_name=f"{supplier}",
                    meal_plan=None,
                    cancellation_policy=None,
                    currency=request.currency.upper(),
                    base_price=price,
                    tax_fee=Decimal("0"),
                    final_price=price,
                    checkin=request.checkin,
                    checkout=request.checkout,
                    adults=request.adults,
                    children=request.children,
                    source_url=request.platform_url,
                    raw_payload={
                        "mode": "google_travel_selectors",
                        "snippet": str(row.get("snippet", ""))[:240],
                    },
                )
            )
            if len(offers) >= 20:
                break
        return offers

    async def _parse_google_price_panel_dom(self, *, page, request: SearchRequest) -> list[RawRateOffer]:
        try:
            rows = await page.eval_on_selector_all(
                "body",
                r"""
                (body) => {
                    const out = [];
                    const priceRe = /(?:US\$|\$|USD)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/gi;
                    const suppliers = ['Booking.com', 'Agoda', 'Trip.com', 'Expedia', 'Hotels.com', 'KAYAK', 'Official website', '官方网站', 'Bluepillow', 'Bluepillow.hk'];
                    const walk = (node, depth) => {
                        if (depth > 8) return;
                        if (!node || node.nodeType !== 1) return;
                        const text = (node.innerText || '').trim();
                        if (text.length < 10 || text.length > 1200) {
                            for (const ch of node.children || []) walk(ch, depth + 1);
                            return;
                        }
                        const matches = [...text.matchAll(priceRe)];
                        if (matches.length === 0) {
                            for (const ch of node.children || []) walk(ch, depth + 1);
                            return;
                        }
                        let supplier = '';
                        for (const s of suppliers) {
                            if (text.includes(s)) {
                                supplier = s;
                                break;
                            }
                        }
                        if (!supplier) {
                            const m = text.match(/([a-z0-9.-]+\.(?:com|cn|net|org|hk))/i);
                            if (m) supplier = m[1];
                        }
                        if (!supplier && node.parentElement) {
                            const pt = (node.parentElement.innerText || '').trim();
                            if (pt.length < 2000) {
                                for (const s of suppliers) {
                                    if (pt.includes(s)) {
                                        supplier = s;
                                        break;
                                    }
                                }
                            }
                        }
                        if (!supplier) {
                            for (const ch of node.children || []) walk(ch, depth + 1);
                            return;
                        }
                        const price = parseFloat(matches[0][1].replace(/,/g, ''));
                        if (isNaN(price) || price < 20 || price > 10000) return;
                        const key = supplier + '|' + price;
                        if (!window.__gSeen) window.__gSeen = new Set();
                        if (window.__gSeen.has(key)) return;
                        window.__gSeen.add(key);
                        out.push({ supplier, price });
                        if (out.length >= 20) return;
                    };
                    window.__gSeen = new Set();
                    walk(body, 0);
                    return out;
                }
                """,
            )
        except Exception:
            return []
        if not rows or not isinstance(rows, list):
            return []
        offers = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            supplier = str(row.get("supplier", "")).strip()
            price = self._to_decimal(row.get("price"))
            if not supplier or price is None or price < Decimal("20"):
                continue
            key = (supplier.lower(), f"{price:.2f}")
            if key in seen:
                continue
            seen.add(key)
            offers.append(
                RawRateOffer(
                    platform=self.platform,
                    platform_hotel_id=request.platform_hotel_id,
                    room_name=f"{supplier}",
                    meal_plan=None,
                    cancellation_policy=None,
                    currency=request.currency.upper(),
                    base_price=price,
                    tax_fee=Decimal("0"),
                    final_price=price,
                    checkin=request.checkin,
                    checkout=request.checkout,
                    adults=request.adults,
                    children=request.children,
                    source_url=request.platform_url,
                    raw_payload={"mode": "google_price_panel"},
                )
            )
            if len(offers) >= 15:
                break
        return offers

    async def _after_load_interactions(self, page, request: SearchRequest | None = None) -> None:
        try:
            await page.mouse.move(100, 200)
            await page.wait_for_timeout(300)
            await page.mouse.move(300, 400)
            await page.wait_for_timeout(200)
            await page.mouse.move(500, 300)
            await page.wait_for_timeout(400)
        except Exception:
            pass

        name_part = (request.platform_hotel_id or "").replace("+", " ").strip()[:30] if request else ""
        try:
            await page.wait_for_function(
                """(namePart) => {
                    const t = (document.body && document.body.innerText) ? document.body.innerText : '';
                    if (/Loading results|Loading map/i.test(t) && t.length < 800) return false;
                    if (namePart && t.includes(namePart)) return true;
                    if (/Prices starting from|Back to list of all results|Compare prices|All options|精选选项|所有选项/i.test(t)) return true;
                    return false;
                }""",
                name_part,
                timeout=20000,
            )
            await page.wait_for_timeout(1800)
        except Exception:
            try:
                await page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.5)")
                await page.wait_for_timeout(3200)
            except Exception:
                pass

        for scroll_ratio in (0.2, 0.45, 0.7, 0.35):
            try:
                await page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {scroll_ratio})")
                await page.wait_for_timeout(1200)
            except Exception:
                pass

        open_selectors = [
            "button:has-text('View prices')",
            "button:has-text('Compare prices')",
            "button:has-text('All options')",
            "button:has-text('See all prices')",
            "button:has-text('View all prices')",
            "button:has-text('精选选项')",
            "button:has-text('所有选项')",
            "button:has-text('价格')",
            "button:has-text('查看价格')",
            "div[role='button']:has-text('Compare prices')",
            "div[role='button']:has-text('All options')",
            "div[role='button']:has-text('精选选项')",
            "div[role='button']:has-text('所有选项')",
            "a:has-text('Compare prices')",
            "a:has-text('All options')",
            "a:has-text('View all prices')",
            "a:has-text('See all prices')",
            "a:has-text('精选选项')",
            "a:has-text('所有选项')",
            "a[aria-label*='Prices starting from']",
            "a[aria-label*='price']",
            "button[aria-label*='price']",
            "button[aria-label*='Price']",
            "button[aria-label*='options']",
            "button[aria-label*='Options']",
            "a[href*='/travel/hotels/'][href*='checkin=']",
            "a[href*='/travel/search'][href*='checkin=']",
        ]
        for selector in open_selectors:
            try:
                locator = page.locator(selector).first
                if await locator.count() == 0:
                    continue
                await locator.scroll_into_view_if_needed(timeout=2500)
                await page.wait_for_timeout(350)
                await locator.click(timeout=2200)
                await page.wait_for_timeout(2600)
            except Exception:
                continue

        if request and getattr(request, "platform_hotel_id", None):
            hotel_name = (request.platform_hotel_id or "").replace("+", " ").strip()
            probes = [hotel_name[:24], hotel_name[:18], hotel_name.split(" ")[0] if hotel_name else ""]
            for probe in probes:
                if not probe:
                    continue
                try:
                    candidate = page.locator(
                        ".ADs2Tc, .CcERhd, a[href*='/travel/'], div[role='button'], button"
                    ).filter(has_text=probe).first
                    if await candidate.count() == 0:
                        continue
                    await candidate.scroll_into_view_if_needed(timeout=2500)
                    await page.wait_for_timeout(400)
                    await candidate.click(timeout=2200)
                    await page.wait_for_timeout(2600)
                except Exception:
                    continue

        for _ in range(3):
            try:
                retry_btn = page.locator("button:has-text('Retry'), button:has-text('重试')").first
                if await retry_btn.count() > 0:
                    await retry_btn.click(timeout=2200)
                    await page.wait_for_timeout(4500)
            except Exception:
                break

        try:
            await page.wait_for_function(
                r"""() => {
                    const t = (document.body && document.body.innerText) ? document.body.innerText : '';
                    if (/Booking|Agoda|Trip\.com|Expedia|Hotels\.com|Bluepillow|Official website|官方网站/.test(t)) return true;
                    if (document.querySelector('.ADs2Tc .iqYCVb, .ADs2Tc .nDkDDb, .CcERhd .iqYCVb, .CcERhd .nDkDDb')) return true;
                    if (/Compare prices|All options|View all prices|精选选项|所有选项/.test(t)) return true;
                    return false;
                }""",
                timeout=12000,
            )
            await page.wait_for_timeout(2200)
        except Exception:
            pass

        for scroll_ratio in (0.3, 0.55, 0.4):
            try:
                await page.evaluate(f"window.scrollTo(0, document.body.scrollHeight * {scroll_ratio})")
                await page.wait_for_timeout(900)
            except Exception:
                pass

    async def _parse_supplier_text_offers(self, *, page, request: SearchRequest) -> list[RawRateOffer]:
        text = await page.inner_text("body")
        lines = [line.strip() for line in text.splitlines() if line.strip()]
        if not lines:
            return []

        joined = " ".join(lines).lower()
        has_detail_signal = any(
            marker in joined
            for marker in (
                "all options",
                "所有选项",
                "selected options",
                "精选选项",
                "view all prices",
                "compare prices",
            )
        )
        if has_detail_signal:
            start_markers = (
                "selected options",
                "精选选项",
                "all options",
                "所有选项",
                "view all prices",
                "compare prices",
            )
            end_markers = (
                "similar hotels",
                "类似酒店",
                "nearby hotels",
                "附近酒店",
            )
            start_idx = 0
            for idx, line in enumerate(lines):
                lower = line.lower()
                if any(marker in lower for marker in start_markers):
                    start_idx = idx
                    break
            end_idx = len(lines)
            for idx in range(start_idx + 1, len(lines)):
                lower = lines[idx].lower()
                if any(marker in lower for marker in end_markers):
                    end_idx = idx
                    break
            segment = lines[start_idx:end_idx]
        else:
            segment = lines

        offers: list[RawRateOffer] = []
        seen: set[tuple[str, str, str]] = set()
        for idx, line in enumerate(segment):
            supplier = self._normalize_supplier_name(line)
            if not supplier:
                continue

            price_value: Decimal | None = None
            currency = request.currency.upper()
            price_idx = -1
            for probe in range(idx + 1, min(idx + 10, len(segment))):
                next_line = segment[probe]
                if probe > idx + 1 and self._normalize_supplier_name(next_line):
                    break
                parsed_price = self._extract_price_and_currency(next_line, default_currency=request.currency)
                if parsed_price is None:
                    continue
                price_value, currency = parsed_price
                price_idx = probe
                break

            if price_value is None or price_value <= Decimal("0"):
                continue

            notes: list[str] = []
            if price_idx > idx:
                for note_idx in range(idx + 1, price_idx):
                    note = segment[note_idx]
                    if self._is_action_line(note):
                        continue
                    if self._extract_price_and_currency(note, default_currency=request.currency):
                        continue
                    notes.append(note)
            cancellation = self._extract_supplier_cancellation(notes)

            dedupe = (supplier.lower(), f"{price_value:.2f}", currency)
            if dedupe in seen:
                continue
            seen.add(dedupe)

            amount = price_value.quantize(Decimal("0.01"))
            offers.append(
                RawRateOffer(
                    platform=self.platform,
                    platform_hotel_id=request.platform_hotel_id,
                    room_name=f"{supplier} offer",
                    meal_plan=None,
                    cancellation_policy=cancellation,
                    currency=currency,
                    base_price=amount,
                    tax_fee=Decimal("0"),
                    final_price=amount,
                    checkin=request.checkin,
                    checkout=request.checkout,
                    adults=request.adults,
                    children=request.children,
                    source_url=request.platform_url,
                    raw_payload={"mode": "supplier_text"},
                )
            )
            if len(offers) >= 20:
                break

        return offers

    def _normalize_supplier_name(self, line: str) -> str | None:
        value = line.strip()
        if not value:
            return None
        lower = value.lower()

        if self._is_action_line(value):
            return None
        if self._extract_price_and_currency(value, default_currency="USD"):
            return None

        if "官方网站" in value or "official website" in lower or "official site" in lower:
            return "Official website"

        domain_match = re.search(r"([a-z0-9.-]+\.(?:com|cn|net|org|co|io|travel|hk))", lower)
        if domain_match:
            domain = domain_match.group(1)
            canonical = {
                "booking.com": "Booking.com",
                "agoda.com": "Agoda",
                "trip.com": "Trip.com",
                "expedia.com": "Expedia.com",
                "hotels.com": "Hotels.com",
                "klook.com": "Klook",
                "travelocity.com": "Travelocity.com",
                "kayak.com": "KAYAK.com",
                "orbitz.com": "Orbitz.com",
                "bluepillow.com": "Bluepillow.com",
                "bluepillow.hk": "Bluepillow.hk",
                "wego.com": "Wego",
            }
            return canonical.get(domain, domain)

        if any(keyword in lower for keyword in self.SUPPLIER_KEYWORDS):
            if "booking" in lower:
                return "Booking.com"
            if "agoda" in lower:
                return "Agoda"
            if "trip" in lower:
                return "Trip.com"
            if "expedia" in lower:
                return "Expedia.com"
            if "hotels" in lower:
                return "Hotels.com"
            if "kayak" in lower:
                return "KAYAK.com"
            if "bluepillow" in lower:
                return "Bluepillow.hk" if ".hk" in lower else "Bluepillow.com"
            return value

        return None

    def _is_action_line(self, line: str) -> bool:
        lower = line.strip().lower()
        return lower.startswith(("visit", "访问")) or ("网站" in lower and "访问" in lower)

    def _extract_price_and_currency(
        self,
        line: str,
        *,
        default_currency: str,
    ) -> tuple[Decimal, str] | None:
        patterns: list[tuple[re.Pattern[str], str]] = [
            (re.compile(r"US\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", flags=re.IGNORECASE), "USD"),
            (re.compile(r"USD\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", flags=re.IGNORECASE), "USD"),
            (re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*(?:per night|nightly|total|/night)?", flags=re.IGNORECASE), "USD"),
            (re.compile(r"[¥￥]\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)", flags=re.IGNORECASE), "CNY"),
        ]
        for pattern, currency in patterns:
            match = pattern.search(line)
            if not match:
                continue
            amount = self._to_decimal(match.group(1))
            if amount is None:
                continue
            if amount < Decimal("20"):
                continue
            return amount, currency

        return None

    def _extract_supplier_cancellation(self, notes: list[str]) -> str | None:
        if not notes:
            return None
        merged = " · ".join(item.strip() for item in notes if item.strip())
        if not merged:
            return None

        lower = merged.lower()
        if "free cancellation" in lower or "免费取消" in merged:
            return merged[:255]
        if "non-refundable" in lower or "不可退款" in merged or "不可退" in merged:
            return merged[:255]

        return merged[:255]

    async def _parse_aria_label_card_offers(self, *, page, request: SearchRequest) -> list[RawRateOffer]:
        rows = await page.eval_on_selector_all(
            "a[aria-label]",
            r"""
            (nodes) => {
              const out = [];
              const seen = new Set();
              const extract = (aria) => {
                const patterns = [
                  /Prices starting from\s*(?:US\$|\$)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*,\s*(.+)$/i,
                  /^(.+?)\s*,\s*(?:US\$|\$)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)$/i,
                  /^(.+?)\s*[，,]\s*(?:¥|￥|€|£)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)$/i,
                ];
                for (const re of patterns) {
                  const m = aria.match(re);
                  if (!m) continue;
                  if (m.length === 3 && /Prices starting from/i.test(m[0])) {
                    return { name: m[2].trim(), price: m[1] };
                  }
                  return { name: m[1].trim(), price: m[2] };
                }
                return null;
              };
              const nightlyRe = /\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*nightly/i;
              const totalRe = /\$\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)\s*total/i;
              for (const node of nodes) {
                const aria = (node.getAttribute('aria-label') || '').trim();
                const parsed = extract(aria);
                if (!parsed) continue;
                const price = parsed.price;
                const name = parsed.name;
                if (!name) continue;
                const text = (node.innerText || '').trim();
                const nightlyMatch = text.match(nightlyRe);
                const totalMatch = text.match(totalRe);
                const key = `${name}|${price}`;
                if (seen.has(key)) continue;
                seen.add(key);
                out.push({
                  name,
                  price,
                  nightly: nightlyMatch ? nightlyMatch[1] : null,
                  total: totalMatch ? totalMatch[1] : null,
                  snippet: text.slice(0, 240),
                });
                if (out.length >= 20) break;
              }
              return out;
            }
            """,
        )
        if not rows:
            return []
        offers: list[RawRateOffer] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name", "")).strip()
            price = self._to_decimal(row.get("nightly") or row.get("price") or row.get("total"))
            if not name or price is None or price < Decimal("20"):
                continue
            key = (name.lower()[:80], f"{price:.2f}")
            if key in seen:
                continue
            seen.add(key)
            offers.append(
                RawRateOffer(
                    platform=self.platform,
                    platform_hotel_id=request.platform_hotel_id,
                    room_name=name[:255],
                    meal_plan=None,
                    cancellation_policy=None,
                    currency=request.currency.upper(),
                    base_price=price,
                    tax_fee=Decimal("0"),
                    final_price=price,
                    checkin=request.checkin,
                    checkout=request.checkout,
                    adults=request.adults,
                    children=request.children,
                    source_url=request.platform_url,
                    raw_payload={
                        "mode": "aria_label_card",
                        "snippet": str(row.get("snippet", ""))[:240],
                    },
                )
            )
        return offers

    async def _parse_listing_dom_offers(self, *, page, request: SearchRequest) -> list[RawRateOffer]:
        try:
            rows = await page.eval_on_selector_all(
                ".ADs2Tc, .CcERhd, a[href*='/travel/'], div[role='button']",
                r"""
                (nodes) => {
                    const out = [];
                    const seen = new Set();
                    const priceRe = /(?:US\$|USD|\$|¥|￥)\s*([0-9][0-9,]*(?:\.[0-9]{1,2})?)/i;
                    const supplierRe = /(Booking\.com|Agoda|Trip\.com|Expedia(?:\.com)?|Hotels\.com|Official website|官方网站|Bluepillow(?:\.hk)?)/i;
                    for (const node of nodes) {
                        const text = (node.innerText || '').trim();
                        if (!text || text.length < 10) continue;
                        const s = text.match(supplierRe);
                        const p = text.match(priceRe);
                        if (!s || !p) continue;
                        const supplier = s[1];
                        const price = parseFloat(p[1].replace(/,/g, ''));
                        if (!Number.isFinite(price) || price < 20 || price > 10000) continue;
                        const key = `${supplier}|${price}`;
                        if (seen.has(key)) continue;
                        seen.add(key);
                        out.push({ supplier, price, snippet: text.slice(0, 240) });
                        if (out.length >= 20) break;
                    }
                    return out;
                }
                """,
            )
        except Exception:
            return []
        if not rows:
            return []
        offers: list[RawRateOffer] = []
        seen: set[tuple[str, str]] = set()
        for row in rows:
            if not isinstance(row, dict):
                continue
            supplier = str(row.get("supplier", "")).strip()
            price = self._to_decimal(row.get("price"))
            if not supplier or price is None or price < Decimal("20"):
                continue
            key = (supplier.lower(), f"{price:.2f}")
            if key in seen:
                continue
            seen.add(key)
            offers.append(
                RawRateOffer(
                    platform=self.platform,
                    platform_hotel_id=request.platform_hotel_id,
                    room_name=supplier[:255],
                    meal_plan=None,
                    cancellation_policy=None,
                    currency=request.currency.upper(),
                    base_price=price,
                    tax_fee=Decimal("0"),
                    final_price=price,
                    checkin=request.checkin,
                    checkout=request.checkout,
                    adults=request.adults,
                    children=request.children,
                    source_url=request.platform_url,
                    raw_payload={
                        "mode": "listing_dom",
                        "snippet": str(row.get("snippet", ""))[:240],
                    },
                )
            )
        return offers

from __future__ import annotations

import asyncio
import json
import hashlib
import logging
import random
import re
import time
from abc import ABC, abstractmethod
from collections import deque
from collections.abc import Iterable
from decimal import InvalidOperation
from decimal import Decimal
from pathlib import Path
from typing import Any
from typing import Awaitable, Callable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from playwright.async_api import Browser, BrowserContext, Page, Playwright, Response

from hotelrate.config import Settings
from hotelrate.core.market_locale import accept_language_header, normalize_bcp47_locale
from hotelrate.schemas import Platform, RawRateOffer, SearchRequest


class BaseCollector(ABC):
    platform: Platform

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = logging.getLogger(self.__class__.__name__)

    DEFAULT_RESPONSE_PATTERNS: tuple[str, ...] = (
        "price",
        "rate",
        "availability",
        "offer",
        "room",
        "hotel",
    )
    ROOM_KEYS: tuple[str, ...] = (
        "roomName",
        "room_name",
        "roomType",
        "room_type",
        "name",
        "title",
    )
    MEAL_KEYS: tuple[str, ...] = (
        "mealPlan",
        "meal_plan",
        "board",
        "boardType",
        "breakfast",
    )
    CANCELLATION_KEYS: tuple[str, ...] = (
        "cancellationPolicy",
        "cancellation_policy",
        "cancelPolicy",
        "freeCancellation",
    )
    PRICE_KEYS: tuple[str, ...] = (
        "price",
        "displayPrice",
        "discountedPrice",
        "amount",
        "value",
        "salePrice",
        "gross_price",
    )
    FINAL_PRICE_KEYS: tuple[str, ...] = (
        "finalPrice",
        "totalPrice",
        "priceAfterTax",
        "bookPrice",
        "payablePrice",
    )
    TAX_KEYS: tuple[str, ...] = ("tax", "taxFee", "taxesAndFees", "vat")
    CURRENCY_KEYS: tuple[str, ...] = ("currency", "currencyCode", "currency_code")
    PRICE_CONTEXT_KEYWORDS: tuple[str, ...] = (
        "night",
        "per night",
        "nightly",
        "total",
        "with taxes",
        "taxes",
        "rate",
        "price",
        "每晚",
        "总价",
        "含税",
        "房价",
        "今晚",
    )
    USER_AGENTS: tuple[str, ...] = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_6) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    )

    @abstractmethod
    async def fetch_rates(self, request: SearchRequest) -> list[RawRateOffer]:
        """Fetch rate offers for one hotel+date query."""

    def build_demo_offer(self, request: SearchRequest, low: int, high: int) -> RawRateOffer:
        price = self._deterministic_price(request, low=low, high=high)
        tax_fee = (price * Decimal("0.12")).quantize(Decimal("0.01"))
        final_price = (price + tax_fee).quantize(Decimal("0.01"))
        return RawRateOffer(
            platform=self.platform,
            platform_hotel_id=request.platform_hotel_id,
            room_name="Standard Room",
            meal_plan="Room only",
            cancellation_policy="Free cancellation before 24h",
            currency=request.currency,
            base_price=price,
            tax_fee=tax_fee,
            final_price=final_price,
            checkin=request.checkin,
            checkout=request.checkout,
            adults=request.adults,
            children=request.children,
            source_url=request.platform_url,
            raw_payload={"mode": "demo"},
        )

    def _deterministic_price(self, request: SearchRequest, low: int, high: int) -> Decimal:
        seed_str = f"{self.platform.value}|{request.platform_hotel_id}|{request.checkin.isoformat()}"
        seed = int(hashlib.sha1(seed_str.encode("utf-8")).hexdigest()[:8], 16)
        rng = random.Random(seed)
        return Decimal(str(rng.randint(low, high))).quantize(Decimal("0.01"))

    def _navigation_timeout_ms(self) -> int:
        timeout_seconds = self.settings.request_timeout_seconds
        if self.platform == Platform.GOOGLE_HOTELS:
            timeout_seconds = max(
                timeout_seconds,
                getattr(self.settings, "google_request_timeout_seconds", timeout_seconds),
            )
        return timeout_seconds * 1000

    async def open_playwright_context(
        self,
        pw: Playwright,
        request: SearchRequest | None = None,
    ) -> tuple[Browser | None, BrowserContext]:
        launch_kwargs: dict[str, Any] = {"headless": self.settings.headless}
        if self.settings.playwright_disable_automation_flags:
            launch_kwargs["args"] = [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--disable-component-extensions-with-background-pages",
                "--disable-default-apps",
                "--disable-extensions",
                "--disable-sync",
                "--metrics-recording-only",
                "--no-first-run",
            ]

        channel = self.settings.playwright_browser_channel.strip()
        user_data_dir = self._resolve_user_data_dir()
        locale = normalize_bcp47_locale(request.locale if request else "en-US")
        context_kwargs: dict[str, Any] = {
            "locale": locale,
            "user_agent": random.choice(self.USER_AGENTS),
            "viewport": {"width": 1360, "height": 900},
        }
        non_persistent_headers = {
            "Accept-Language": accept_language_header(locale),
            "Upgrade-Insecure-Requests": "1",
        }

        if channel and channel != "chromium":
            launch_kwargs["channel"] = channel

        if user_data_dir:
            context = await pw.chromium.launch_persistent_context(
                user_data_dir=user_data_dir,
                **context_kwargs,
                **launch_kwargs,
            )
            await context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
            )
            return None, context

        browser = await pw.chromium.launch(**launch_kwargs)
        context = await browser.new_context(
            **context_kwargs,
            extra_http_headers=non_persistent_headers,
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
        )
        return browser, context

    def _resolve_user_data_dir(self) -> str:
        platform = getattr(self, "platform", None)
        if platform == Platform.TRIP:
            value = self.settings.playwright_user_data_dir_trip.strip()
            if value:
                return value
        elif platform == Platform.BOOKING:
            value = self.settings.playwright_user_data_dir_booking.strip()
            if value:
                return value
        elif platform == Platform.AGODA:
            value = self.settings.playwright_user_data_dir_agoda.strip()
            if value:
                return value
        elif platform == Platform.GOOGLE_HOTELS:
            value = self.settings.playwright_user_data_dir_google_hotels.strip()
            if value:
                return value
            # Google Hotels does not require a logged-in session. Avoid reusing a
            # shared persistent profile by default because it can destabilize the
            # Playwright driver or cross-pollute state from other OTAs.
            return ""
        global_value = self.settings.playwright_user_data_dir.strip()
        if global_value:
            return global_value

        default_path = self._default_profile_path(platform)
        if default_path:
            return default_path
        return ""

    def _default_profile_path(self, platform: Platform | None) -> str:
        if platform is None:
            return ""
        root = Path(__file__).resolve().parents[3]
        folder = f"{platform.value}-profile"
        candidate = root / ".playwright" / folder
        if candidate.exists() and candidate.is_dir():
            return str(candidate)
        return ""

    async def close_playwright_context(
        self,
        *,
        browser: Browser | None,
        context: BrowserContext,
    ) -> None:
        if browser is None:
            await context.close()
            return
        await browser.close()

    async def capture_json_payloads(
        self,
        *,
        page: Page,
        url: str,
        response_patterns: Iterable[str] | None = None,
        settle_ms: int = 2500,
        after_load: Callable[[Page], Awaitable[None]] | None = None,
    ) -> list[dict[str, Any]]:
        patterns = tuple(response_patterns or self.DEFAULT_RESPONSE_PATTERNS)
        payloads: list[dict[str, Any]] = []
        pending: set[asyncio.Task[None]] = set()

        def on_response(response: Response) -> None:
            task = asyncio.create_task(
                self._collect_response_payload(
                    response=response,
                    patterns=patterns,
                    output=payloads,
                )
            )
            pending.add(task)
            task.add_done_callback(pending.discard)

        page.on("response", on_response)
        try:
            timeout_ms = self._navigation_timeout_ms()
            await page.goto(
                url,
                wait_until="load",
                timeout=timeout_ms,
            )
            try:
                await page.wait_for_load_state(
                    "networkidle",
                    timeout=timeout_ms,
                )
            except Exception:
                self.logger.debug("networkidle timeout on %s", url)

            if after_load is not None:
                try:
                    await after_load(page)
                except Exception as exc:
                    self.logger.debug("after_load interaction failed: %s", exc)

            jitter = random.randint(250, 900)
            await page.wait_for_timeout(settle_ms + jitter)
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
        finally:
            page.remove_listener("response", on_response)

        return payloads

    async def _collect_response_payload(
        self,
        *,
        response: Response,
        patterns: tuple[str, ...],
        output: list[dict[str, Any]],
    ) -> None:
        if response.request.resource_type not in {"xhr", "fetch"}:
            return

        lower_url = response.url.lower()
        if not any(pattern.lower() in lower_url for pattern in patterns):
            return

        data = await self._safe_read_response_json(response)
        if data is None:
            return

        output.append(
            {
                "url": response.url,
                "status": response.status,
                "data": data,
            }
        )

    async def _safe_read_response_json(self, response: Response) -> Any | None:
        try:
            return await response.json()
        except Exception:
            pass

        try:
            text = await response.text()
        except Exception:
            return None

        text = text.strip()
        if not text:
            return None

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    def parse_payloads_to_offers(
        self,
        *,
        request: SearchRequest,
        payloads: list[dict[str, Any]],
        room_keys: Iterable[str] | None = None,
        price_keys: Iterable[str] | None = None,
        final_price_keys: Iterable[str] | None = None,
        tax_keys: Iterable[str] | None = None,
        meal_keys: Iterable[str] | None = None,
        cancellation_keys: Iterable[str] | None = None,
        currency_keys: Iterable[str] | None = None,
        max_offers: int = 20,
    ) -> list[RawRateOffer]:
        room_lookup = tuple(room_keys or self.ROOM_KEYS)
        price_lookup = tuple(price_keys or self.PRICE_KEYS)
        final_lookup = tuple(final_price_keys or self.FINAL_PRICE_KEYS)
        tax_lookup = tuple(tax_keys or self.TAX_KEYS)
        meal_lookup = tuple(meal_keys or self.MEAL_KEYS)
        cancel_lookup = tuple(cancellation_keys or self.CANCELLATION_KEYS)
        currency_lookup = tuple(currency_keys or self.CURRENCY_KEYS)

        offers: list[RawRateOffer] = []
        seen: set[tuple[str, str, str]] = set()

        for payload in payloads:
            for node in self._walk_dicts(payload.get("data")):
                room_name = self._extract_text(node, room_lookup)
                if not room_name:
                    continue

                final_price = self._extract_decimal(node, final_lookup)
                base_price = self._extract_decimal(node, price_lookup)
                tax_fee = self._extract_decimal(node, tax_lookup) or Decimal("0")
                if final_price is None:
                    final_price = self._extract_decimal_deep(node, final_lookup, depth=2)
                if base_price is None:
                    base_price = self._extract_decimal_deep(node, price_lookup, depth=2)
                if tax_fee == Decimal("0"):
                    deep_tax = self._extract_decimal_deep(node, tax_lookup, depth=2)
                    if deep_tax is not None:
                        tax_fee = deep_tax

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
                if final_price < Decimal("20"):
                    continue

                currency = (self._extract_text(node, currency_lookup) or request.currency).upper()
                meal_plan = self._extract_text(node, meal_lookup)
                cancellation_policy = self._extract_text(node, cancel_lookup)

                dedupe_key = (room_name.strip().lower(), f"{final_price:.2f}", currency)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)

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
                        source_url=payload.get("url") or request.platform_url,
                        raw_payload={
                            "source_url": payload.get("url"),
                            "sample_node": self._clip_dict(node),
                        },
                    )
                )

                if len(offers) >= max_offers:
                    return offers

        return offers

    async def parse_ld_json_fallback(
        self,
        *,
        page: Page,
        request: SearchRequest,
        max_offers: int = 10,
    ) -> list[RawRateOffer]:
        scripts = await page.eval_on_selector_all(
            "script[type='application/ld+json']",
            "nodes => nodes.map(node => node.textContent || '')",
        )

        payloads: list[dict[str, Any]] = []
        for script in scripts:
            content = str(script).strip()
            if not content:
                continue
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                continue
            payloads.append({"url": request.platform_url, "status": 200, "data": data})

        return self.parse_payloads_to_offers(request=request, payloads=payloads, max_offers=max_offers)

    async def parse_text_price_fallback(
        self,
        *,
        page: Page,
        request: SearchRequest,
        min_price: Decimal = Decimal("20"),
        max_price: Decimal = Decimal("10000"),
        max_offers: int = 8,
    ) -> list[RawRateOffer]:
        text = await page.inner_text("body")
        prices = self._extract_prices_from_text(
            text=text,
            currency=request.currency,
            min_price=min_price,
            max_price=max_price,
            max_results=max_offers,
        )

        offers: list[RawRateOffer] = []
        for idx, price in enumerate(prices, start=1):
            offers.append(
                RawRateOffer(
                    platform=self.platform,
                    platform_hotel_id=request.platform_hotel_id,
                    room_name=f"Web Offer #{idx}",
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
                    raw_payload={"mode": "text_fallback"},
                )
            )
        return offers

    def parse_text_price_fallback_from_text(
        self,
        *,
        body_text: str,
        request: SearchRequest,
        min_price: Decimal = Decimal("20"),
        max_price: Decimal = Decimal("10000"),
        max_offers: int = 8,
        mode: str = "text_fallback",
    ) -> list[RawRateOffer]:
        """Parse prices from plain body text (e.g. from Firecrawl HTML/markdown)."""
        prices = self._extract_prices_from_text(
            text=body_text,
            currency=request.currency,
            min_price=min_price,
            max_price=max_price,
            max_results=max_offers,
        )
        offers: list[RawRateOffer] = []
        for idx, price in enumerate(prices, start=1):
            offers.append(
                RawRateOffer(
                    platform=self.platform,
                    platform_hotel_id=request.platform_hotel_id,
                    room_name=f"Web Offer #{idx}",
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
                    raw_payload={"mode": mode},
                )
            )
        return offers

    def count_structured_offers(self, offers: list[RawRateOffer]) -> int:
        total = 0
        for offer in offers:
            mode = str(offer.raw_payload.get("mode", "")).strip().lower()
            if mode in {"text_fallback", "url_fallback", "demo", "firecrawl_fallback"}:
                continue
            total += 1
        return total

    def _walk_dicts(self, data: Any) -> Iterable[dict[str, Any]]:
        if isinstance(data, dict):
            yield data
            for value in data.values():
                yield from self._walk_dicts(value)
            return
        if isinstance(data, list):
            for value in data:
                yield from self._walk_dicts(value)

    def _extract_text(self, data: dict[str, Any], keys: Iterable[str]) -> str | None:
        for key in keys:
            value = self._value_from_variants(data, key)
            if value is None:
                continue
            if isinstance(value, str):
                cleaned = value.strip()
                if cleaned:
                    return cleaned
            elif isinstance(value, (int, float)):
                return str(value)
        return None

    def _extract_decimal(self, data: dict[str, Any], keys: Iterable[str]) -> Decimal | None:
        for key in keys:
            value = self._value_from_variants(data, key)
            number = self._to_decimal(value)
            if number is not None:
                return number
        return None

    def _extract_decimal_deep(self, data: dict[str, Any], keys: Iterable[str], depth: int = 2) -> Decimal | None:
        if depth <= 0:
            return None
        queue: deque[tuple[Any, int]] = deque([(data, depth)])
        while queue:
            current, level = queue.popleft()
            if isinstance(current, dict):
                direct = self._extract_decimal(current, keys)
                if direct is not None:
                    return direct
                if level > 1:
                    for value in current.values():
                        queue.append((value, level - 1))
            elif isinstance(current, list) and level > 1:
                for value in current[:10]:
                    queue.append((value, level - 1))
        return None

    def _value_from_variants(self, data: dict[str, Any], key: str) -> Any | None:
        if key in data:
            return data[key]

        lower_key = key.lower()
        for actual_key, value in data.items():
            normalized = actual_key.replace("-", "_").replace(" ", "_").lower()
            if normalized == lower_key.lower():
                return value
        return None

    def _to_decimal(self, value: Any) -> Decimal | None:
        if value is None:
            return None
        if isinstance(value, bool):
            return None
        if isinstance(value, Decimal):
            return value
        if isinstance(value, (int, float)):
            return Decimal(str(value))
        if isinstance(value, str):
            cleaned = re.sub(r"[^0-9.,-]", "", value.strip())
            if not cleaned:
                return None
            if cleaned.count(",") > 0 and cleaned.count(".") == 0:
                cleaned = cleaned.replace(",", ".")
            else:
                cleaned = cleaned.replace(",", "")
            try:
                return Decimal(cleaned)
            except InvalidOperation:
                return None
        return None

    def _save_debug_payloads(self, platform_name: str, payloads: list[dict[str, Any]]) -> None:
        """Save raw API payloads to reports/ for debugging."""
        try:
            reports_dir = Path("reports")
            reports_dir.mkdir(parents=True, exist_ok=True)
            path = reports_dir / f"{platform_name}_debug_{int(time.time() * 1000)}.json"
            path.write_text(json.dumps(payloads, indent=2, default=str), encoding="utf-8")
            self.logger.info("%s debug: saved %d payloads to %s", platform_name, len(payloads), path)
        except Exception as exc:
            self.logger.debug("%s debug save failed: %s", platform_name, exc)

    def _clip_dict(self, data: dict[str, Any], max_items: int = 24) -> dict[str, Any]:
        clipped: dict[str, Any] = {}
        for idx, (key, value) in enumerate(data.items()):
            if idx >= max_items:
                break
            if isinstance(value, (str, int, float, bool)) or value is None:
                clipped[key] = value
            else:
                clipped[key] = type(value).__name__
        return clipped

    def merge_url_query(self, url: str, updates: dict[str, Any]) -> str:
        parsed = urlparse(url)
        existing = dict(parse_qsl(parsed.query, keep_blank_values=True))

        for key, value in updates.items():
            if value is None:
                continue
            existing[key] = str(value)

        query = urlencode(existing, doseq=True)
        return urlunparse(parsed._replace(query=query))

    def remove_url_query_keys(self, url: str, drop_keys: Iterable[str]) -> str:
        parsed = urlparse(url)
        if not parsed.query:
            return url

        drop_lookup = {item.lower() for item in drop_keys}
        pairs = parse_qsl(parsed.query, keep_blank_values=True)
        kept = [(k, v) for k, v in pairs if k.lower() not in drop_lookup]
        query = urlencode(kept, doseq=True)
        return urlunparse(parsed._replace(query=query))

    def _extract_prices_from_text(
        self,
        *,
        text: str,
        currency: str,
        min_price: Decimal,
        max_price: Decimal,
        max_results: int,
    ) -> list[Decimal]:
        currency = currency.upper()
        currency_tokens = (
            currency,
            "USD",
            "US$",
            "$",
            "CNY",
            "RMB",
            "HKD",
            "SGD",
            "EUR",
            "GBP",
            "JPY",
            "CAD",
            "AUD",
            "¥",
            "￥",
            "€",
            "£",
        )
        token_pattern = "|".join(re.escape(token) for token in currency_tokens)
        with_currency = re.compile(
            rf"(?:{token_pattern})\s*([0-9]{{1,6}}(?:,[0-9]{{3}})*(?:\.[0-9]{{1,2}})?)",
            flags=re.IGNORECASE,
        )
        with_suffix = re.compile(
            r"([0-9]{2,6}(?:,[0-9]{3})*(?:\.[0-9]{1,2})?)\s*(?:/night|per night|nightly|每晚|总价|total)",
            flags=re.IGNORECASE,
        )

        found: list[Decimal] = []
        fallback_found: list[Decimal] = []
        seen: set[str] = set()

        def append_price(raw: str, target: list[Decimal]) -> None:
            number = self._to_decimal(raw)
            if number is None:
                return
            if number < min_price or number > max_price:
                return
            key = f"{number:.2f}"
            if key in seen:
                return
            seen.add(key)
            target.append(number.quantize(Decimal("0.01")))

        for match in with_currency.finditer(text):
            amount = match.group(1)
            start, end = match.span()
            window = text[max(0, start - 48) : min(len(text), end + 48)].lower()
            has_context = any(keyword in window for keyword in self.PRICE_CONTEXT_KEYWORDS)
            if has_context:
                append_price(amount, found)
            else:
                append_price(amount, fallback_found)
            if len(found) >= max_results:
                return found[:max_results]

        if len(found) < max_results:
            for match in with_suffix.finditer(text):
                append_price(match.group(1), found)
                if len(found) >= max_results:
                    return found[:max_results]

        if found:
            return found[:max_results]

        return fallback_found[:max_results]

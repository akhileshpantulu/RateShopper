from __future__ import annotations

import logging
import re
from html import unescape
from urllib.parse import parse_qs, parse_qsl, quote_plus, unquote, urlencode, urljoin, urlparse, urlunparse

import httpx

from hotelrate.config import Settings
from hotelrate.core.market_locale import (
    accept_language_header,
    agoda_locale_path_prefix,
    booking_lang_param,
    google_hl_gl,
    normalize_bcp47_locale,
    trip_locale_query_value,
)
from hotelrate.schemas import Platform
from hotelrate.services.firecrawl_client import FirecrawlClient


class UrlDiscoveryService:
    DOMAIN_MAP = {
        Platform.TRIP: "trip.com",
        Platform.BOOKING: "booking.com",
        Platform.AGODA: "agoda.com",
        Platform.GOOGLE_HOTELS: "google.com",
    }
    SEARCH_USER_AGENT = (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 13_6_6) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"
    )

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self.logger = logging.getLogger(self.__class__.__name__)
        self.firecrawl = FirecrawlClient(settings)

    async def discover(
        self,
        *,
        platform: Platform,
        hotel_name: str,
        city: str,
        checkin: str,
        checkout: str,
        adults: int,
        children: int,
        currency: str,
        known_url: str | None,
        locale: str = "en-US",
        market: str | None = None,
    ) -> list[str]:
        candidates: list[tuple[str, int]] = []
        if known_url:
            candidates.append((known_url, 120))

        for url in self._generated_platform_urls(
                platform=platform,
                hotel_name=hotel_name,
                city=city,
                checkin=checkin,
                checkout=checkout,
                adults=adults,
                children=children,
                currency=currency,
                known_url=known_url,
                locale=locale,
                market=market,
            ):
            candidates.append((url, 90))

        if platform == Platform.GOOGLE_HOTELS:
            return await self._finalize_candidates(
                platform=platform,
                hotel_name=hotel_name,
                city=city,
                checkin=checkin,
                checkout=checkout,
                adults=adults,
                children=children,
                currency=currency,
                candidates=candidates,
                locale=locale,
                market=market,
            )

        search_links = await self._search_engine_links(
            platform=platform,
            hotel_name=hotel_name,
            city=city,
        )
        for url, base_score in search_links:
            candidates.append((url, base_score))

        html_extracted = await self._extract_detail_urls_from_web_pages(
            platform=platform,
            seed_urls=[item for item, _ in candidates],
        )
        for url in html_extracted:
            candidates.append((url, 75))

        detail_seed_count_now = sum(1 for item, _ in candidates if self.is_detail_url(platform, item))
        if detail_seed_count_now < 2:
            pw_extracted = await self._extract_detail_urls_with_playwright(
                platform=platform,
                seed_urls=[item for item, _ in candidates],
                locale=locale,
            )
            for url in pw_extracted:
                candidates.append((url, 82))

        detail_seed_count = sum(1 for item, _ in candidates if self.is_detail_url(platform, item))
        if detail_seed_count < 2:
            seed_urls: list[str] = []
            for item, _ in candidates:
                if item in seed_urls:
                    continue
                seed_urls.append(item)
                if len(seed_urls) >= 2:
                    break

            expanded: list[tuple[str, int]] = []
            for url in seed_urls:
                try:
                    mapped = await self.firecrawl.map_url(url)
                except Exception:
                    mapped = []
                for mapped_url in mapped[:20]:
                    if self._is_platform_url(platform, mapped_url):
                        expanded.append((mapped_url, 55))

                try:
                    crawled = await self.firecrawl.crawl_url(
                        url,
                        max_depth=2,
                        limit=8,
                        poll_rounds=3,
                        poll_interval_seconds=1.0,
                    )
                except Exception:
                    crawled = []
                for crawled_url in crawled[:20]:
                    if self._is_platform_url(platform, crawled_url):
                        expanded.append((crawled_url, 50))

            candidates.extend(expanded)

        return await self._finalize_candidates(
            platform=platform,
            hotel_name=hotel_name,
            city=city,
            checkin=checkin,
            checkout=checkout,
            adults=adults,
            children=children,
            currency=currency,
            candidates=candidates,
            locale=locale,
            market=market,
        )

    async def _finalize_candidates(
        self,
        *,
        platform: Platform,
        hotel_name: str,
        city: str,
        checkin: str,
        checkout: str,
        adults: int,
        children: int,
        currency: str,
        candidates: list[tuple[str, int]],
        locale: str = "en-US",
        market: str | None = None,
    ) -> list[str]:
        normalized: list[tuple[str, int]] = []
        for raw_url, base_score in candidates:
            if not isinstance(raw_url, str):
                continue
            normalized_url = self._normalize_url(raw_url)
            if not normalized_url:
                continue
            if not self._is_platform_url(platform, normalized_url):
                continue
            normalized_url = self._canonicalize_platform_url(
                platform=platform,
                url=normalized_url,
                checkin=checkin,
                checkout=checkout,
                adults=adults,
                children=children,
                currency=currency,
                locale=locale,
                market=market,
            )
            score = base_score + self._score_url(
                platform=platform,
                url=normalized_url,
                hotel_name=hotel_name,
                city=city,
                checkin=checkin,
                checkout=checkout,
            )
            normalized.append((normalized_url, score))

        ranked = self._dedupe_ranked(normalized)
        ranked = await self._rerank_with_page_signals(
            platform=platform,
            hotel_name=hotel_name,
            city=city,
            ranked=ranked,
        )
        detailed = [(url, score) for url, score in ranked if self.is_detail_url(platform, url)]
        if detailed:
            others = [(url, score) for url, score in ranked if not self.is_detail_url(platform, url)]
            merged = detailed + others
            return [item for item, _ in merged[:10]]
        return [item for item, _ in ranked[:10]]

    def _generated_platform_urls(
        self,
        *,
        platform: Platform,
        hotel_name: str,
        city: str,
        checkin: str,
        checkout: str,
        adults: int,
        children: int,
        currency: str,
        known_url: str | None,
        locale: str = "en-US",
        market: str | None = None,
    ) -> list[str]:
        query_text = f"{hotel_name} {city}".strip()

        if platform == Platform.GOOGLE_HOTELS:
            hl, gl = google_hl_gl(locale, market)
            query = urlencode(
                {
                    "q": query_text,
                    "checkin": checkin,
                    "checkout": checkout,
                    "adults": adults,
                    "children": children,
                    "hl": hl,
                    "gl": gl,
                }
            )
            return [f"https://www.google.com/travel/search?{query}"]

        if platform == Platform.BOOKING:
            urls: list[str] = []
            if known_url:
                urls.append(known_url)
                parsed = urlparse(known_url)
                base_path = re.sub(r"\.[a-z]{2}(?:-[a-z]{2})?\.html$", ".html", parsed.path)
                urls.append(urlunparse(parsed._replace(path=base_path)))
                urls.append(urlunparse(parsed._replace(path=base_path.replace(".html", ".en-gb.html"))))
                urls.append(urlunparse(parsed._replace(path=base_path.replace(".html", ".zh-cn.html"))))
            search_query = urlencode(
                {
                    "ss": query_text,
                    "checkin": checkin,
                    "checkout": checkout,
                    "group_adults": adults,
                    "group_children": children,
                    "no_rooms": 1,
                    "selected_currency": currency,
                    "lang": booking_lang_param(locale),
                }
            )
            urls.append(f"https://www.booking.com/searchresults.html?{search_query}")
            return urls

        if platform == Platform.AGODA:
            urls = []
            if known_url:
                urls.append(known_url)
                urls.append(known_url.replace("/zh-hk/", "/en-us/"))
                urls.append(known_url.replace("/en-us/", "/zh-hk/"))
                urls.append(known_url.replace("/zh-hk/", "/en-gb/"))
            search_query = urlencode(
                {
                    "checkIn": checkin,
                    "los": 1,
                    "adults": adults,
                    "children": children,
                    "rooms": 1,
                    "currencyCode": currency,
                    "textToSearch": query_text,
                }
            )
            agoda_pre = agoda_locale_path_prefix(locale)
            urls.append(f"https://www.agoda.com/{agoda_pre}/search?{search_query}")
            return urls

        if platform == Platform.TRIP:
            urls = []
            if known_url:
                urls.append(known_url)
                parsed = urlparse(known_url)
                params = dict(parse_qsl(parsed.query, keep_blank_values=True))
                hotel_id = params.get("hotelId", "").strip()
                if hotel_id:
                    detail_query = urlencode(
                        {
                            "hotelId": hotel_id,
                            "checkIn": checkin,
                            "checkOut": checkout,
                            "adult": adults,
                            "children": children,
                            "curr": currency,
                            "barcurr": currency,
                            "locale": trip_locale_query_value(locale),
                        }
                    )
                    urls.append(f"https://www.trip.com/hotels/detail/?{detail_query}")
            search_query = urlencode(
                {
                    "keyword": query_text,
                    "checkin": checkin,
                    "checkout": checkout,
                    "adult": adults,
                    "children": children,
                }
            )
            urls.append(f"https://www.trip.com/hotels/list?{search_query}")
            urls.append(f"https://www.trip.com/hotels/list/?{search_query}")
            return urls

        return []

    async def _search_engine_links(
        self,
        *,
        platform: Platform,
        hotel_name: str,
        city: str,
    ) -> list[tuple[str, int]]:
        queries = self._build_search_queries(platform=platform, hotel_name=hotel_name, city=city)[:3]
        weighted: list[tuple[str, int]] = []

        for query in queries:
            yahoo_results = await self._run_yahoo_query(query)
            for item in yahoo_results:
                weighted.append((item, 86))
            if len(weighted) >= 12:
                break

            brave_results = await self._run_brave_query(query)
            for item in brave_results:
                weighted.append((item, 80))
            if len(weighted) >= 12:
                break

            if not yahoo_results and not brave_results:
                duck_results = await self._run_duckduckgo_query(query)
                for item in duck_results:
                    weighted.append((item, 72))
            if len(weighted) >= 12:
                break

        out: list[tuple[str, int]] = []
        seen: set[str] = set()
        for url, score in weighted:
            if url in seen:
                continue
            seen.add(url)
            out.append((url, score))
        return out

    def _build_search_queries(self, *, platform: Platform, hotel_name: str, city: str) -> list[str]:
        domain = self.DOMAIN_MAP[platform]
        query_text = f"{hotel_name} {city}".strip()
        queries = [
            f'site:{domain} "{hotel_name}" "{city}"',
            f"site:{domain} {query_text}",
            f'site:{domain} "{hotel_name}" hotel',
        ]
        if platform == Platform.TRIP:
            queries.append(f"site:{domain} {query_text} hotel-detail")
        elif platform == Platform.BOOKING:
            queries.append(f"site:{domain} {query_text} /hotel/")
        elif platform == Platform.AGODA:
            queries.append(f"site:{domain} {query_text} /hotel/")
        return self._dedupe(queries)[:4]

    async def _run_duckduckgo_query(self, query: str) -> list[str]:
        url = f"https://duckduckgo.com/html/?q={quote_plus(query)}"
        timeout = min(self.settings.request_timeout_seconds, 8)
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(
                    url,
                    headers={
                        "User-Agent": self.SEARCH_USER_AGENT,
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                )
        except Exception as exc:
            self.logger.debug("duckduckgo query failed q=%s err=%s", query, exc)
            return []

        if resp.status_code != 200:
            return []

        html = resp.text
        raw_links = re.findall(r'class="result__a" href="([^"]+)"', html)
        cleaned: list[str] = []
        for raw in raw_links:
            candidate = self._decode_duckduckgo_redirect(raw)
            if candidate and candidate.startswith("http"):
                cleaned.append(candidate)
        return cleaned

    async def _run_yahoo_query(self, query: str) -> list[str]:
        url = f"https://search.yahoo.com/search?p={quote_plus(query)}"
        timeout = min(self.settings.request_timeout_seconds, 10)
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(
                    url,
                    headers={
                        "User-Agent": self.SEARCH_USER_AGENT,
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                )
        except Exception as exc:
            self.logger.debug("yahoo query failed q=%s err=%s", query, exc)
            return []

        if resp.status_code != 200:
            return []

        html = resp.text
        raw_links = re.findall(r"https://r\.search\.yahoo\.com/[^\s\"'<>]+", html)
        decoded: list[str] = []
        for item in raw_links:
            candidate = self._decode_yahoo_redirect(item)
            if candidate and candidate.startswith("http"):
                decoded.append(candidate)

        for href in re.findall(r'href="([^"]+)"', html, flags=re.IGNORECASE):
            cleaned = self._sanitize_search_link(href)
            if cleaned:
                decoded.append(cleaned)
        return self._dedupe(decoded)

    def _decode_yahoo_redirect(self, url: str) -> str:
        match = re.search(r"/RU=([^/]+)/RK=", url)
        if not match:
            return ""
        decoded = unquote(match.group(1))
        if decoded.startswith("http"):
            return decoded
        return ""

    async def _run_brave_query(self, query: str) -> list[str]:
        url = f"https://search.brave.com/search?q={quote_plus(query)}&source=web"
        timeout = min(self.settings.request_timeout_seconds, 8)
        try:
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                resp = await client.get(
                    url,
                    headers={
                        "User-Agent": self.SEARCH_USER_AGENT,
                        "Accept-Language": "en-US,en;q=0.9",
                    },
                )
        except Exception as exc:
            self.logger.debug("brave query failed q=%s err=%s", query, exc)
            return []

        if resp.status_code != 200:
            return []
        html = resp.text
        if "Too many requests" in html or "Error 429" in html:
            return []

        links: list[str] = []
        links.extend(self._extract_http_links(html))
        for href in re.findall(r'href="([^"]+)"', html, flags=re.IGNORECASE):
            cleaned = self._sanitize_search_link(href)
            if cleaned:
                links.append(cleaned)
        return self._dedupe(links)

    def _extract_http_links(self, text: str) -> list[str]:
        matches = re.findall(r"https?://[^\s\"'<>]+", text)
        cleaned: list[str] = []
        for item in matches:
            cleaned_item = item.strip().rstrip(").,]")
            if cleaned_item.startswith("http"):
                cleaned.append(cleaned_item)
        return cleaned

    def _sanitize_search_link(self, href: str) -> str:
        if not href:
            return ""
        candidate = unquote(href).strip()
        if candidate.startswith("//"):
            candidate = "https:" + candidate
        if candidate.startswith("http"):
            return candidate
        if candidate.startswith("/url?"):
            parsed = urlparse(candidate)
            q = parse_qs(parsed.query)
            direct = q.get("q", [""])[0]
            if direct.startswith("http"):
                return direct
        return ""

    def _decode_duckduckgo_redirect(self, url: str) -> str:
        if not url:
            return ""
        if not url.startswith("//duckduckgo.com/l/?"):
            return unquote(url)

        parsed = urlparse("https:" + url)
        params = dict(parse_qsl(parsed.query, keep_blank_values=True))
        encoded = params.get("uddg", "")
        if not encoded:
            return ""
        return unquote(encoded)

    async def _extract_detail_urls_from_web_pages(
        self,
        *,
        platform: Platform,
        seed_urls: list[str],
    ) -> list[str]:
        candidates: list[str] = []
        for raw_url in seed_urls:
            if not isinstance(raw_url, str):
                continue
            if not raw_url.startswith("http"):
                continue
            parsed = urlparse(raw_url)
            if self.is_detail_url(platform, raw_url):
                continue
            path = parsed.path.lower()
            if platform == Platform.TRIP and "/hotels/list" not in path and "/search" not in path:
                continue
            if platform == Platform.BOOKING and "/searchresults" not in path:
                continue
            if platform == Platform.AGODA and "/search" not in path and path not in {"", "/"}:
                continue
            candidates.append(raw_url)
            if len(candidates) >= 4:
                break

        if not candidates:
            return []

        found: list[str] = []
        try:
            async with httpx.AsyncClient(timeout=self.settings.request_timeout_seconds, follow_redirects=True) as client:
                for url in candidates:
                    try:
                        resp = await client.get(
                            url,
                            headers={
                                "User-Agent": self.SEARCH_USER_AGENT,
                                "Accept-Language": "en-US,en;q=0.8",
                            },
                        )
                    except Exception:
                        continue
                    if resp.status_code != 200:
                        continue
                    links = self._extract_detail_links_from_html(
                        platform=platform,
                        base_url=url,
                        html=resp.text,
                    )
                    found.extend(links)
        except Exception as exc:
            self.logger.debug("detail extraction from web pages failed: %s", exc)
            return []

        return self._dedupe(found)

    async def _extract_detail_urls_with_playwright(
        self,
        *,
        platform: Platform,
        seed_urls: list[str],
        locale: str = "en-US",
    ) -> list[str]:
        candidates: list[str] = []
        for raw_url in seed_urls:
            if not isinstance(raw_url, str) or not raw_url.startswith("http"):
                continue
            if self.is_detail_url(platform, raw_url):
                continue
            if not self._is_platform_url(platform, raw_url):
                continue
            parsed = urlparse(raw_url)
            path = parsed.path.lower()
            if platform == Platform.TRIP and "/hotels/list" not in path and "/search" not in path:
                continue
            if platform == Platform.BOOKING and "/searchresults" not in path:
                continue
            if platform == Platform.AGODA and "/search" not in path and path not in {"", "/"}:
                continue
            candidates.append(raw_url)
            if len(candidates) >= 3:
                break

        if not candidates:
            return []

        found: list[str] = []
        try:
            from playwright.async_api import async_playwright
        except Exception:
            return []

        launch_kwargs: dict[str, object] = {"headless": True}
        if self.settings.playwright_disable_automation_flags:
            launch_kwargs["args"] = ["--disable-blink-features=AutomationControlled"]
        channel = self.settings.playwright_browser_channel.strip()
        if channel and channel != "chromium":
            launch_kwargs["channel"] = channel

        try:
            norm_locale = normalize_bcp47_locale(locale)
            async with async_playwright() as pw:
                browser = await pw.chromium.launch(**launch_kwargs)
                context = await browser.new_context(
                    locale=norm_locale,
                    user_agent=self.SEARCH_USER_AGENT,
                    extra_http_headers={"Accept-Language": accept_language_header(norm_locale)},
                    viewport={"width": 1360, "height": 900},
                )
                await context.add_init_script(
                    "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
                )
                page = await context.new_page()
                for url in candidates:
                    try:
                        await page.goto(
                            url,
                            wait_until="domcontentloaded",
                            timeout=self.settings.request_timeout_seconds * 1000,
                        )
                    except Exception:
                        continue
                    await self._maybe_bypass_booking_consent(page)
                    try:
                        await page.wait_for_load_state(
                            "networkidle",
                            timeout=min(self.settings.request_timeout_seconds, 12) * 1000,
                        )
                    except Exception:
                        pass
                    await page.wait_for_timeout(1800)

                    dom_links = await self._collect_detail_links_from_dom(
                        platform=platform,
                        page_url=page.url,
                        page=page,
                    )
                    found.extend(dom_links)

                    try:
                        html = await page.content()
                    except Exception:
                        html = ""
                    if html:
                        found.extend(
                            self._extract_detail_links_from_html(
                                platform=platform,
                                base_url=page.url,
                                html=html,
                            )
                        )
                await context.close()
                await browser.close()
        except Exception as exc:
            self.logger.debug("playwright extraction failed platform=%s err=%s", platform.value, exc)
            return []

        cleaned: list[str] = []
        for url in self._dedupe(found):
            if not self._is_platform_url(platform, url):
                continue
            if not self.is_detail_url(platform, url):
                continue
            cleaned.append(url)
        return cleaned

    async def _collect_detail_links_from_dom(
        self,
        *,
        platform: Platform,
        page_url: str,
        page,
    ) -> list[str]:
        try:
            hrefs = await page.eval_on_selector_all(
                "a[href]",
                "nodes => nodes.map((node) => node.getAttribute('href') || '').filter(Boolean)",
            )
        except Exception:
            return []
        if not isinstance(hrefs, list):
            return []

        out: list[str] = []
        for href in hrefs:
            if not isinstance(href, str):
                continue
            candidate = href.strip()
            if not candidate:
                continue
            if candidate.startswith("/"):
                candidate = urljoin(page_url, candidate)
            candidate = self._sanitize_search_link(candidate) or candidate
            if not candidate.startswith("http"):
                continue
            if self._is_platform_url(platform, candidate) and self.is_detail_url(platform, candidate):
                out.append(candidate)
        return out

    async def _maybe_bypass_booking_consent(self, page) -> None:
        lower_url = str(page.url).lower()
        if "booking.com/pipl_consent" not in lower_url:
            return
        parsed = urlparse(page.url)
        target_page = parse_qs(parsed.query).get("target_page", [""])[0]
        target_page = unquote(target_page)
        if target_page.startswith("http"):
            try:
                await page.goto(target_page, wait_until="domcontentloaded", timeout=45000)
                return
            except Exception:
                pass

        selectors = [
            "button#onetrust-accept-btn-handler",
            "button[data-testid='consent-banner-accept-all']",
            "button:has-text('Accept')",
            "button:has-text('同意')",
        ]
        for selector in selectors:
            locator = page.locator(selector).first
            try:
                if await locator.count() == 0:
                    continue
                await locator.click(timeout=1500)
                await page.wait_for_timeout(1200)
                break
            except Exception:
                continue

    def _extract_detail_links_from_html(self, *, platform: Platform, base_url: str, html: str) -> list[str]:
        normalized_html = unescape(html).replace("\\/", "/")
        patterns: list[str] = []
        if platform == Platform.TRIP:
            patterns = [
                r'https://(?:www|us|sg|in|uk|ph)\.trip\.com/hotels/detail/\?[^"\'\s>]+',
                r'https://(?:www|us|sg|in|uk|ph)\.trip\.com/hotels/[^"\'\s>]*hotel-detail-\d+[^"\'\s>]*',
                r'/hotels/[^"\'\s>]*hotel-detail-\d+[^"\'\s>]*',
            ]
        elif platform == Platform.BOOKING:
            patterns = [
                r'https://www\.booking\.com/hotel/[^"\'\s>]+\.html',
                r'/hotel/[^"\'\s>]+\.html',
            ]
        elif platform == Platform.AGODA:
            patterns = [
                r'https://www\.agoda\.com/[^"\'\s>]+/hotel/[^"\'\s>]+\.html',
                r'/[^"\'\s>]+/hotel/[^"\'\s>]+\.html',
            ]

        links: list[str] = []
        for pattern in patterns:
            for item in re.findall(pattern, normalized_html, flags=re.IGNORECASE):
                link = item.strip()
                if link.startswith("/"):
                    link = urljoin(base_url, link)
                links.append(link)
        return links

    def is_detail_url(self, platform: Platform, url: str) -> bool:
        parsed = urlparse(url)
        path = parsed.path.lower()
        query_keys = {key.lower() for key, _ in parse_qsl(parsed.query, keep_blank_values=True)}

        if platform == Platform.TRIP:
            if "/hotels/detail/" in path:
                return True
            if "-hotel-detail-" in path:
                return True
            return "hotelid" in query_keys

        if platform == Platform.BOOKING:
            if not (path.startswith("/hotel/") and path.endswith(".html")):
                return False
            parts = [item for item in path.strip("/").split("/") if item]
            if len(parts) < 3:
                return False
            if parts[-1] in {"index.html", "hotel.html"}:
                return False
            return True

        if platform == Platform.AGODA:
            if not ("/hotel/" in path and path.endswith(".html")):
                return False
            if path.endswith("/hotel.html"):
                return False
            return True

        if platform == Platform.GOOGLE_HOTELS:
            return "/travel/search" in path

        return False

    def _is_platform_url(self, platform: Platform, url: str) -> bool:
        domain = self.DOMAIN_MAP[platform]
        host = urlparse(url).netloc.lower()
        return domain in host

    def _normalize_url(self, url: str) -> str:
        parsed = urlparse(url)
        if not parsed.scheme:
            return ""
        cleaned_query = urlencode(dict(parse_qsl(parsed.query, keep_blank_values=True)), doseq=True)
        return urlunparse(parsed._replace(fragment="", query=cleaned_query))

    def _canonicalize_platform_url(
        self,
        *,
        platform: Platform,
        url: str,
        checkin: str,
        checkout: str,
        adults: int,
        children: int,
        currency: str,
        locale: str = "en-US",
        market: str | None = None,
    ) -> str:
        parsed = urlparse(url)
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))

        if platform == Platform.TRIP:
            match = re.search(r"-hotel-detail-(\d+)", parsed.path.lower())
            if match:
                hotel_id = match.group(1)
                detail_query = urlencode(
                    {
                        "hotelId": hotel_id,
                        "checkIn": checkin,
                        "checkOut": checkout,
                        "adult": adults,
                        "children": children,
                        "curr": currency,
                        "barcurr": currency,
                        "locale": trip_locale_query_value(locale),
                    }
                )
                return f"https://www.trip.com/hotels/detail/?{detail_query}"
            if "/hotels/detail/" in parsed.path and "hotelId" in query:
                query.update(
                    {
                        "checkIn": checkin,
                        "checkOut": checkout,
                        "adult": str(adults),
                        "children": str(children),
                        "curr": currency,
                        "barcurr": currency,
                        "locale": trip_locale_query_value(locale),
                    }
                )
                return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

        if platform == Platform.BOOKING and parsed.path.startswith("/hotel/"):
            path = re.sub(r"\.[a-z]{2}(?:-[a-z]{2})?\.html$", ".html", parsed.path, flags=re.IGNORECASE)
            query.update(
                {
                    "checkin": checkin,
                    "checkout": checkout,
                    "group_adults": str(adults),
                    "group_children": str(children),
                    "no_rooms": "1",
                    "selected_currency": currency,
                    "lang": booking_lang_param(locale),
                }
            )
            return urlunparse(parsed._replace(netloc="www.booking.com", path=path, query=urlencode(query, doseq=True)))

        if platform == Platform.AGODA and "/hotel/" in parsed.path:
            query.update(
                {
                    "checkIn": checkin,
                    "los": "1",
                    "adults": str(adults),
                    "children": str(children),
                    "rooms": "1",
                    "currencyCode": currency,
                }
            )
            return urlunparse(parsed._replace(netloc="www.agoda.com", query=urlencode(query, doseq=True)))

        if platform == Platform.GOOGLE_HOTELS and "/travel/search" in parsed.path:
            hl, gl = google_hl_gl(locale, market)
            query.update(
                {
                    "checkin": checkin,
                    "checkout": checkout,
                    "adults": str(adults),
                    "children": str(children),
                    "hl": hl,
                    "gl": gl,
                }
            )
            return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

        return url

    def _dedupe(self, urls: list[str]) -> list[str]:
        seen: set[str] = set()
        out: list[str] = []
        for url in urls:
            if not url or url in seen:
                continue
            seen.add(url)
            out.append(url)
        return out

    def _score_url(
        self,
        *,
        platform: Platform,
        url: str,
        hotel_name: str,
        city: str,
        checkin: str,
        checkout: str,
    ) -> int:
        parsed = urlparse(url)
        path = parsed.path.lower()
        query = dict(parse_qsl(parsed.query, keep_blank_values=True))
        score = 0
        if self.is_detail_url(platform, url):
            score += 80

        if platform == Platform.TRIP:
            if "/hotels/detail/" in path:
                score += 45
            if "hotelid" in {k.lower() for k in query.keys()}:
                score += 35
            if "checkin" in {k.lower() for k in query.keys()}:
                score += 15
            if any(item in path for item in ("/review", "/photo", "/map")):
                score -= 28

        if platform == Platform.BOOKING:
            if self.is_detail_url(platform, url):
                score += 45
            elif path.startswith("/hotel/"):
                score += 8
            if "/searchresults" in path:
                score += 10
            if query.get("checkin") == checkin and query.get("checkout") == checkout:
                score += 25
            if any(item in path for item in ("/reviews", "/facilities", "/location")):
                score -= 20
            if path.endswith("/hotel/index.html"):
                score -= 25

        if platform == Platform.AGODA:
            if "/hotel/" in path and path.endswith(".html"):
                score += 45
            if "/search" in path:
                score += 12
            if query.get("checkIn") == checkin:
                score += 18
            if any(item in path for item in ("/reviews", "/map")):
                score -= 16

        if platform == Platform.GOOGLE_HOTELS:
            if "/travel/search" in path:
                score += 45
            if query.get("checkin") == checkin and query.get("checkout") == checkout:
                score += 22

        token_source = f"{hotel_name} {city}".lower()
        path_and_query = f"{path} {parsed.query}".lower()
        for token in re.findall(r"[a-z0-9]+", token_source):
            if len(token) < 3:
                continue
            if token in path_and_query:
                score += 2
        detail_slug = path.replace("-", " ")
        score += self._token_overlap_score(token_source, detail_slug, weight=5)

        return score

    def _token_overlap_score(self, left: str, right: str, *, weight: int = 10) -> int:
        left_tokens = {item for item in re.findall(r"[a-z0-9]+", left.lower()) if len(item) >= 3}
        right_tokens = {item for item in re.findall(r"[a-z0-9]+", right.lower()) if len(item) >= 3}
        if not left_tokens or not right_tokens:
            return 0
        overlap = len(left_tokens & right_tokens)
        if overlap <= 0:
            return 0
        return min(weight, overlap * 2)

    async def _rerank_with_page_signals(
        self,
        *,
        platform: Platform,
        hotel_name: str,
        city: str,
        ranked: list[tuple[str, int]],
    ) -> list[tuple[str, int]]:
        if not ranked:
            return ranked

        target = f"{hotel_name} {city}".strip()
        to_check: list[tuple[str, int]] = []
        remain: list[tuple[str, int]] = []
        for item in ranked:
            if len(to_check) < 2 and self.is_detail_url(platform, item[0]):
                to_check.append(item)
            else:
                remain.append(item)
        if not to_check:
            return ranked

        boosted: list[tuple[str, int]] = []
        try:
            timeout = min(self.settings.request_timeout_seconds, 7)
            async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
                for url, base_score in to_check:
                    bonus = 0
                    try:
                        resp = await client.get(
                            url,
                            headers={
                                "User-Agent": self.SEARCH_USER_AGENT,
                                "Accept-Language": "en-US,en;q=0.9",
                            },
                        )
                        if resp.status_code == 200:
                            text = resp.text
                            page_text = self._extract_title_and_h1(text)
                            bonus = self._token_overlap_score(target, page_text, weight=18)
                    except Exception:
                        bonus = 0
                    boosted.append((url, base_score + bonus))
        except Exception:
            return ranked

        merged = boosted + remain
        return self._dedupe_ranked(merged)

    def _extract_title_and_h1(self, html: str) -> str:
        title = ""
        h1 = ""
        title_match = re.search(r"<title[^>]*>(.*?)</title>", html, flags=re.IGNORECASE | re.DOTALL)
        if title_match:
            title = unescape(title_match.group(1))
        h1_match = re.search(r"<h1[^>]*>(.*?)</h1>", html, flags=re.IGNORECASE | re.DOTALL)
        if h1_match:
            h1 = unescape(re.sub(r"<[^>]+>", " ", h1_match.group(1)))
        return f"{title} {h1}".strip()

    def _dedupe_ranked(self, pairs: list[tuple[str, int]]) -> list[tuple[str, int]]:
        best: dict[str, int] = {}
        for url, score in pairs:
            prev = best.get(url)
            if prev is None or score > prev:
                best[url] = score
        ranked = sorted(best.items(), key=lambda item: item[1], reverse=True)
        return ranked

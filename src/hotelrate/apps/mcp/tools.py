"""MCP tool definitions for hotelrate.

Each tool is a thin wrapper around the existing LiveQuoteService.
Progress events are bridged to MCP context notifications.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from datetime import date, timedelta
from typing import Any

from mcp.server.fastmcp import Context, FastMCP

from hotelrate.config import Settings
from hotelrate.schemas import (
    BatchQuoteRequest,
    LiveQuoteRequest,
    Platform,
)
from hotelrate.rateshop import CollectorError, GoogleHotelsCollector, rate_shop, sample_dates
from hotelrate.services import LiveQuoteService

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_PLATFORMS = {p.value for p in Platform}


def _resolve_platforms(raw: list[str] | None) -> list[Platform] | None:
    """Convert user-supplied platform strings to Platform enums, or None for all."""
    if not raw:
        return None
    result: list[Platform] = []
    for name in raw:
        low = name.strip().lower()
        if low in _VALID_PLATFORMS:
            result.append(Platform(low))
        else:
            raise ValueError(
                f"Unknown platform '{name}'. "
                f"Valid platforms: {sorted(_VALID_PLATFORMS)}"
            )
    return result or None


def _default_dates(
    checkin: str | None,
    checkout: str | None,
    settings: Settings,
) -> tuple[date, date]:
    """Return (checkin, checkout) dates, falling back to settings defaults."""
    if checkin:
        ci = date.fromisoformat(checkin)
    else:
        ci = date.today() + timedelta(days=settings.default_checkin_offset_days)

    if checkout:
        co = date.fromisoformat(checkout)
    else:
        co = ci + timedelta(days=settings.default_length_of_stay)

    if co <= ci:
        raise ValueError(f"checkout ({co}) must be after checkin ({ci})")
    return ci, co


def _build_progress_bridge(
    ctx: Context,
) -> Callable[[str, str, dict[str, Any]], Awaitable[None]]:
    """Create an on_progress callback that forwards events to MCP context."""

    platform_index: dict[str, int] = {}
    total_platforms: int = 0

    async def on_progress(platform_key: str, phase: str, data: dict[str, Any]) -> None:
        nonlocal total_platforms

        if phase == "start":
            idx = len(platform_index)
            platform_index[platform_key] = idx
            total_platforms = max(total_platforms, idx + 1)
            await ctx.info(f"Starting {platform_key} crawl...")

        elif phase == "attempt":
            url = data.get("url", "?")
            ci = data.get("checkin", "")
            co = data.get("checkout", "")
            is_firecrawl = data.get("firecrawl_fallback", False)
            prefix = "[firecrawl fallback] " if is_firecrawl else ""
            await ctx.info(
                f"  {platform_key}: {prefix}trying {url} ({ci} ~ {co})"
            )

        elif phase == "attempt_done":
            n_offers = data.get("offer_count", 0)
            n_struct = data.get("structured_count", 0)
            error = data.get("error")
            if error:
                await ctx.info(f"  {platform_key}: error — {error}")
            else:
                await ctx.info(
                    f"  {platform_key}: {n_struct} structured / {n_offers} total offers"
                )

        elif phase == "done":
            idx = platform_index.get(platform_key, 0)
            success = data.get("success", False)
            diag = data.get("diagnosis", "")
            elapsed = data.get("elapsed", 0)
            status = "done" if success else "failed"
            await ctx.info(
                f"  {platform_key}: {status} ({diag}) [{elapsed:.1f}s]"
            )
            await ctx.report_progress(idx + 1, total_platforms)

    return on_progress


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


def register_tools(mcp: FastMCP) -> None:
    """Register all hotelrate MCP tools on the given server."""

    @mcp.tool()
    async def hotel_quote(
        ctx: Context,
        hotel_name: str,
        checkin: str | None = None,
        checkout: str | None = None,
        city: str = "",
        adults: int = 2,
        children: int = 0,
        currency: str = "USD",
        platforms: list[str] | None = None,
        locale: str = "en-US",
    ) -> dict[str, Any]:
        """Get real-time hotel prices from multiple OTA platforms.

        Queries Trip.com, Booking.com, Agoda, and/or Google Hotels for current
        room rates. Returns structured price data including room names, prices,
        taxes, meal plans, and cancellation policies.

        Args:
            hotel_name: Hotel name to search for (e.g. "Park Hyatt Tokyo")
            checkin: Check-in date in YYYY-MM-DD format (default: 7 days from now)
            checkout: Check-out date in YYYY-MM-DD format (default: checkin + 1)
            city: City name to help URL discovery (e.g. "Tokyo")
            adults: Number of adults (1-8, default: 2)
            children: Number of children (0-6, default: 0)
            currency: ISO 4217 currency code (default: "USD")
            platforms: List of platforms to query. Options: "trip", "booking",
                      "agoda", "google_hotels". Default: all enabled platforms.
            locale: BCP 47 locale (default: "en-US")
        """
        service: LiveQuoteService = ctx.request_context.lifespan_context["service"]
        settings: Settings = ctx.request_context.lifespan_context["settings"]

        # In live mode, block early if preflight detected critical problems
        if not settings.demo_mode:
            preflight_issues: list[dict[str, Any]] = (
                ctx.request_context.lifespan_context.get("preflight_issues", [])
            )
            errors = [i for i in preflight_issues if i["level"] == "error"]
            if errors:
                fix_cmds = [e["fix_command"] for e in errors]
                raise ValueError(
                    "Environment not ready for live mode. "
                    "Please fix the following issues first:\n"
                    + "\n".join(f"  - {e['message']}" for e in errors)
                    + "\n\nFix commands:\n"
                    + "\n".join(f"  {cmd}" for cmd in fix_cmds)
                )

        ci, co = _default_dates(checkin, checkout, settings)
        platform_list = _resolve_platforms(platforms)

        request = LiveQuoteRequest(
            hotel_name=hotel_name,
            city=city,
            checkin=ci,
            checkout=co,
            adults=adults,
            children=children,
            currency=currency,
            platforms=platform_list,
            locale=locale,
        )

        progress_cb = _build_progress_bridge(ctx)
        response = await service.quote(request, on_progress=progress_cb)
        return response.model_dump(mode="json")

    @mcp.tool()
    async def hotel_quote_batch(
        ctx: Context,
        hotels: list[dict[str, Any]],
        concurrency: int = 3,
    ) -> dict[str, Any]:
        """Get real-time prices for multiple hotels in parallel.

        Each hotel entry in the list uses the same parameters as hotel_quote.

        Args:
            hotels: List of hotel query dicts. Each must have "hotel_name" and
                    optionally "checkin", "checkout", "city", "adults", "children",
                    "currency", "platforms", "locale".
            concurrency: Max number of hotels to query concurrently (1-10, default: 3)
        """
        service: LiveQuoteService = ctx.request_context.lifespan_context["service"]
        settings: Settings = ctx.request_context.lifespan_context["settings"]

        # In live mode, block early if preflight detected critical problems
        if not settings.demo_mode:
            preflight_issues: list[dict[str, Any]] = (
                ctx.request_context.lifespan_context.get("preflight_issues", [])
            )
            errors = [i for i in preflight_issues if i["level"] == "error"]
            if errors:
                fix_cmds = [e["fix_command"] for e in errors]
                raise ValueError(
                    "Environment not ready for live mode. "
                    "Please fix the following issues first:\n"
                    + "\n".join(f"  - {e['message']}" for e in errors)
                    + "\n\nFix commands:\n"
                    + "\n".join(f"  {cmd}" for cmd in fix_cmds)
                )

        requests: list[LiveQuoteRequest] = []
        for entry in hotels:
            ci, co = _default_dates(
                entry.get("checkin"),
                entry.get("checkout"),
                settings,
            )
            platform_list = _resolve_platforms(entry.get("platforms"))
            requests.append(
                LiveQuoteRequest(
                    hotel_name=entry["hotel_name"],
                    city=entry.get("city", ""),
                    checkin=ci,
                    checkout=co,
                    adults=entry.get("adults", 2),
                    children=entry.get("children", 0),
                    currency=entry.get("currency", "USD"),
                    platforms=platform_list,
                    locale=entry.get("locale", "en-US"),
                )
            )

        batch_request = BatchQuoteRequest(hotels=requests, concurrency=concurrency)
        progress_cb = _build_progress_bridge(ctx)
        response = await service.batch_quote(batch_request, on_progress=progress_cb)
        return response.model_dump(mode="json")

    @mcp.tool()
    async def list_platforms(ctx: Context) -> dict[str, Any]:
        """List all supported OTA platforms and their current status.

        Returns platform names, whether they are enabled, and any relevant
        configuration notes (e.g., Google Hotels being skipped by default).
        """
        settings: Settings = ctx.request_context.lifespan_context["settings"]

        platforms = []
        for p in Platform:
            info: dict[str, Any] = {
                "name": p.value,
                "label": {
                    "trip": "Trip.com",
                    "booking": "Booking.com",
                    "agoda": "Agoda",
                    "google_hotels": "Google Hotels",
                }.get(p.value, p.value),
                "enabled": True,
            }
            if p == Platform.GOOGLE_HOTELS and settings.google_skip:
                info["enabled"] = False
                info["note"] = "Skipped by default (GOOGLE_SKIP=true). Pass platforms=['google_hotels'] to force."
            platforms.append(info)

        return {
            "platforms": platforms,
            "demo_mode": settings.demo_mode,
            "default_currency": "USD",
            "default_locale": settings.default_locale,
        }

    @mcp.tool()
    async def health_check(ctx: Context) -> dict[str, Any]:
        """Check system health and configuration status.

        Returns information about the runtime environment including demo mode,
        Playwright availability, Firecrawl connectivity, platform readiness,
        and any preflight issues detected at startup.

        **Recommended first call** — if ``issues`` is non-empty, guide the user
        through the ``setup_commands`` before attempting live crawls.
        """
        settings: Settings = ctx.request_context.lifespan_context["settings"]
        preflight_issues: list[dict[str, Any]] = ctx.request_context.lifespan_context.get(
            "preflight_issues", []
        )

        # Check Playwright availability
        playwright_ok = True
        playwright_note = ""
        try:
            import importlib.util

            playwright_ok = importlib.util.find_spec("playwright") is not None
            browser_target = settings.playwright_browser_channel.strip() or "chromium"
            if playwright_ok:
                playwright_note = (
                    f"channel={settings.playwright_browser_channel}, headless={settings.headless}"
                )
            else:
                playwright_note = (
                    "playwright not installed — "
                    f"run: pip install playwright && playwright install {browser_target}"
                )
        except Exception:
            playwright_ok = False
            browser_target = settings.playwright_browser_channel.strip() or "chromium"
            playwright_note = (
                "playwright not installed — "
                f"run: pip install playwright && playwright install {browser_target}"
            )

        # Check Firecrawl connectivity
        firecrawl_ok = False
        firecrawl_note = ""
        if settings.firecrawl_enabled:
            try:
                import httpx

                async with httpx.AsyncClient(timeout=5) as client:
                    resp = await client.get(f"{settings.firecrawl_base_url}/health")
                    firecrawl_ok = resp.status_code == 200
                    firecrawl_note = f"url={settings.firecrawl_base_url}"
            except Exception as exc:
                firecrawl_note = f"url={settings.firecrawl_base_url}, error={exc}"
        else:
            firecrawl_note = "disabled"

        # Collect fix commands from error/warning-level preflight issues
        setup_commands = [
            issue["fix_command"]
            for issue in preflight_issues
            if issue["level"] in ("error", "warning")
        ]

        return {
            "status": "ok",
            "demo_mode": settings.demo_mode,
            "playwright": {
                "available": playwright_ok,
                "note": playwright_note,
            },
            "firecrawl": {
                "available": firecrawl_ok,
                "enabled": settings.firecrawl_enabled,
                "note": firecrawl_note,
            },
            "platforms": {
                p.value: {
                    "enabled": not (p == Platform.GOOGLE_HOTELS and settings.google_skip),
                }
                for p in Platform
            },
            "config": {
                "headless": settings.headless,
                "default_locale": settings.default_locale,
                "default_checkin_offset_days": settings.default_checkin_offset_days,
                "default_length_of_stay": settings.default_length_of_stay,
            },
            "issues": preflight_issues,
            "setup_commands": setup_commands,
        }

    # -- Google Hotels rate shop: direct calls, no browser, always live (DEMO_MODE does not apply) --------------

    def _google(ctx: Context) -> GoogleHotelsCollector:
        return ctx.request_context.lifespan_context["google"]

    @mcp.tool()
    async def google_find_hotel(
        ctx: Context, name: str, city: str = "", max_candidates: int = 5
    ) -> dict[str, Any]:
        """Find a hotel on Google Hotels and list candidates to confirm.

        Returns up to max_candidates hotels with their source_id (Google property token), star class, rating and
        review count. Show them to the user to confirm the right property, then pass its source_id as `hotel` to
        the other google_* tools. Live call, about 1-2 seconds.
        """
        try:
            found = await _google(ctx).resolve_hotel(name, city, max_candidates=max_candidates)
        except CollectorError as error:
            raise ValueError(f"{type(error).__name__}: {error}") from error
        return {"candidates": [candidate.model_dump(mode="json") for candidate in found]}

    @mcp.tool()
    async def google_price_calendar(
        ctx: Context,
        hotel: str,
        start: str | None = None,
        days: int = 365,
        nights: int = 1,
        adults: int = 2,
        currency: str | None = None,
        include_days: bool = True,
    ) -> dict[str, Any]:
        """Price of the same stay for every check-in date, about 300 days ahead, in one live call.

        Args:
            hotel: source_id from google_find_hotel (preferred), a Google Hotels link, or "name, city". A name is
                   refused unless Google's first result matches it.
            start: first check-in date YYYY-MM-DD (default today)
            days: check-in dates to cover, 1-400 (default 365)
            nights: length of stay priced for each date (default 1)
            currency: ISO 4217 code (default DEFAULT_CURRENCY)
            include_days: false returns only the summary (horizon, counts, lowest/median/highest total)

        Each day has status priced, no_price (inside Google's horizon but no price shown: a possible sell-out) or
        not_offered (beyond the last date Google prices).
        """
        try:
            result = await _google(ctx).price_calendar(
                hotel,
                start=date.fromisoformat(start) if start else None,
                days=days,
                nights=nights,
                adults=adults,
                currency=currency,
            )
        except CollectorError as error:
            raise ValueError(f"{type(error).__name__}: {error}") from error
        data = result.model_dump(mode="json")
        if not include_days:
            data.pop("days")
        return data

    @mcp.tool()
    async def google_hotel_offers(
        ctx: Context,
        hotel: str,
        checkin: str,
        nights: int = 1,
        adults: int = 2,
        currency: str | None = None,
    ) -> dict[str, Any]:
        """Every booking channel's live price for one stay: Booking.com, Expedia, Agoda, the hotel's own site...

        Args:
            hotel: source_id from google_find_hotel (preferred), a Google Hotels link, or "name, city"
            checkin: check-in date YYYY-MM-DD
            nights: nights (default 1)
            currency: ISO 4217 code (default DEFAULT_CURRENCY)

        One row per channel, or per room rate when Google lists rooms, with official-site and sponsored flags.
        """
        try:
            result = await _google(ctx).channel_offers(
                hotel, check_in=date.fromisoformat(checkin), nights=nights, adults=adults, currency=currency
            )
        except CollectorError as error:
            raise ValueError(f"{type(error).__name__}: {error}") from error
        return result.model_dump(mode="json")

    @mcp.tool()
    async def google_rate_shop(
        ctx: Context,
        hotel: str,
        start: str | None = None,
        days: int = 365,
        nights: int = 1,
        adults: int = 2,
        currency: str | None = None,
        offer_dates: list[str] | None = None,
        sample_weeks: bool = False,
        include_days: bool = False,
    ) -> dict[str, Any]:
        """Full rate shop for one hotel: the price calendar plus every channel's offers on chosen dates.

        Args:
            hotel: source_id from google_find_hotel (preferred), a Google Hotels link, or "name, city"
            start, days, nights, adults, currency: as for google_price_calendar
            offer_dates: check-in dates (YYYY-MM-DD) to fetch every channel's offers for
            sample_weeks: also fetch offers for one full week a month (Monday-Sunday from the second Monday)
            include_days: include every calendar day (default false: calendar summary only)

        Calls follow the request policy (2 in flight, 3-8 s apart by default), so each offers date adds about
        5 seconds; sample_weeks over a year is about 84 dates, roughly 7 minutes.
        """
        first = date.fromisoformat(start) if start else None
        dates = [date.fromisoformat(item) for item in offer_dates or []]
        if sample_weeks:
            dates += sample_dates(first or date.today(), months=max(1, days // 30))

        async def progress(done: int, total: int, message: str) -> None:
            await ctx.report_progress(done, total)
            await ctx.info(message)

        try:
            result = await rate_shop(
                _google(ctx),
                hotel,
                start=first,
                days=days,
                nights=nights,
                adults=adults,
                currency=currency,
                offer_dates=dates,
                on_progress=progress,
            )
        except CollectorError as error:
            raise ValueError(f"{type(error).__name__}: {error}") from error
        data = result.model_dump(mode="json")
        if not include_days:
            data["calendar"].pop("days")
        return data

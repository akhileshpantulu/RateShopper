"""FastMCP server for hotelrate — exposes hotel price comparison as MCP tools.

Supports three transports: stdio (default), SSE, and streamable-http.
"""

from __future__ import annotations

import argparse
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from mcp.server.fastmcp import FastMCP

from hotelrate.apps.mcp.preflight import check_environment
from hotelrate.config import get_settings
from hotelrate.rateshop import GoogleHotelsCollector
from hotelrate.services import LiveQuoteService

logger = logging.getLogger(__name__)


@asynccontextmanager
async def _lifespan(server: FastMCP) -> AsyncIterator[dict]:
    """Initialize a shared LiveQuoteService instance for the server lifetime."""
    settings = get_settings()
    issues = check_environment(settings)

    # Log error-level issues to stderr so operators see them immediately.
    for issue in issues:
        if issue["level"] == "error":
            logger.warning(
                "Preflight: %s — fix: %s",
                issue["message"],
                issue["fix_command"],
            )

    service = LiveQuoteService(settings)
    google = GoogleHotelsCollector(settings)
    try:
        yield {"service": service, "settings": settings, "preflight_issues": issues, "google": google}
    finally:
        await google.aclose()


mcp = FastMCP(
    "hotelrate",
    instructions=(
        "Real-time multi-platform hotel price comparison. "
        "Query hotel rates across Trip.com, Booking.com, Agoda, and Google Hotels. "
        "Use hotel_quote for single-hotel lookups, hotel_quote_batch for bulk queries, "
        "list_platforms to see supported OTAs, and health_check to verify the system. "
        "For fast rate shopping without a browser, use the google_* tools: google_find_hotel to pick the hotel, "
        "google_price_calendar for a year of prices in one call, google_hotel_offers for every booking "
        "channel on one date, and google_rate_shop for both."
    ),
    lifespan=_lifespan,
)

# Register tools from the tools module — import here to trigger @mcp.tool() decorators
from hotelrate.apps.mcp.tools import register_tools  # noqa: E402

register_tools(mcp)


def _add_serve_args(parser: argparse.ArgumentParser) -> None:
    """Add --transport / --host / --port flags to *parser*."""
    parser.add_argument(
        "--transport",
        choices=["stdio", "sse", "streamable-http"],
        default="stdio",
        help="MCP transport (default: stdio)",
    )
    parser.add_argument(
        "--host",
        default="127.0.0.1",
        help="Host for SSE/HTTP transport (default: 127.0.0.1)",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=8080,
        help="Port for SSE/HTTP transport (default: 8080)",
    )


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="hotelrate-mcp",
        description="Hotel rate MCP server & CLI tools",
    )
    sub = parser.add_subparsers(dest="command")

    # ── serve ──────────────────────────────────────────────────────────
    serve = sub.add_parser("serve", help="Run the MCP server (default)")
    _add_serve_args(serve)

    # ── setup ──────────────────────────────────────────────────────────
    setup = sub.add_parser("setup", help="Interactive first-time setup wizard")
    setup.add_argument(
        "--platforms",
        nargs="*",
        choices=["trip", "booking", "agoda"],
        help="Platforms to configure (default: interactive prompt)",
    )
    setup.add_argument(
        "--skip-login",
        action="store_true",
        help="Skip browser login steps (useful for CI)",
    )
    setup.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Seconds to wait for manual login per platform (default: 300)",
    )

    # ── doctor ─────────────────────────────────────────────────────────
    doctor = sub.add_parser("doctor", help="Check environment health")
    doctor.add_argument(
        "--probe",
        action="store_true",
        help="Also run live session probes (needs browser profiles)",
    )
    doctor.add_argument(
        "--json",
        action="store_true",
        dest="json_output",
        help="Output machine-readable JSON to stdout",
    )

    # ── find / shop (Google Hotels rate shop) ──────────────────────────
    from hotelrate.apps.mcp.cli.rateshop import add_find_arguments, add_shop_arguments

    find = sub.add_parser("find", help="List Google Hotels candidates for a hotel name")
    add_find_arguments(find)
    shop = sub.add_parser("shop", help="Google Hotels price calendar and channel offers for one hotel")
    add_shop_arguments(shop)

    # ── Top-level flags for backward compat ────────────────────────────
    # ``hotelrate-mcp --transport sse`` still works (command = None → serve)
    _add_serve_args(parser)

    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    """CLI entry point for ``hotelrate-mcp``."""
    args = _parse_args(argv)

    if args.command == "setup":
        from hotelrate.apps.mcp.cli import run_setup

        run_setup(args)
    elif args.command == "find":
        from hotelrate.apps.mcp.cli.rateshop import run_find

        run_find(args)
    elif args.command == "shop":
        from hotelrate.apps.mcp.cli.rateshop import run_shop

        run_shop(args)
    elif args.command == "doctor":
        from hotelrate.apps.mcp.cli import run_doctor

        run_doctor(args)
    else:
        # None (bare flags) or "serve" — run the MCP server.
        run_kwargs: dict = {"transport": args.transport}
        if args.transport in ("sse", "streamable-http"):
            run_kwargs["host"] = args.host
            run_kwargs["port"] = args.port
        mcp.run(**run_kwargs)


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from datetime import date
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hotelrate.config import get_settings
from hotelrate.schemas import BatchQuoteRequest, LiveQuoteRequest, Platform
from hotelrate.services import LiveQuoteService

try:
    from rich.live import Live
    RICH_AVAILABLE = True
except ImportError:
    RICH_AVAILABLE = False


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run live meta quote across Trip/Booking/Google/Agoda")
    parser.add_argument("--hotel", default="")
    parser.add_argument("--city", default="")
    parser.add_argument("--checkin", default="", help="YYYY-MM-DD")
    parser.add_argument("--checkout", default="", help="YYYY-MM-DD")
    parser.add_argument("--adults", type=int, default=2)
    parser.add_argument("--children", type=int, default=0)
    parser.add_argument("--currency", default="USD")
    parser.add_argument("--platforms", default="trip,booking,google_hotels,agoda")
    parser.add_argument("--trip-url", default="")
    parser.add_argument("--booking-url", default="")
    parser.add_argument("--google-url", default="")
    parser.add_argument("--agoda-url", default="")
    parser.add_argument("--probe-days", type=int, default=12)
    parser.add_argument("--probe-step", type=int, default=3)
    parser.add_argument(
        "--batch-config",
        default="",
        help="Path to a JSON file in BatchQuoteRequest shape (or a plain list of LiveQuoteRequest items).",
    )
    parser.add_argument(
        "--batch-concurrency",
        type=int,
        default=3,
        help="Default concurrency for --batch-config when the JSON file does not provide one. Google batch items are still auto-serialized.",
    )
    parser.add_argument(
        "--enable-google",
        action="store_true",
        help="Force-enable Google Hotels even when GOOGLE_SKIP=true in .env. When requested, system node + Google-specific stability defaults are applied automatically when available.",
    )
    parser.add_argument(
        "--use-system-playwright-node",
        action="store_true",
        help="Force-use `node` from PATH as Playwright driver runtime instead of the bundled Node binary. Usually unnecessary for Google because the script auto-enables it when possible.",
    )
    parser.add_argument("--out", default="")
    parser.add_argument("--demo-mode", action="store_true", help="Use deterministic demo offers.")
    parser.add_argument("--rich", action="store_true", help="Show live Rich TUI dashboard during crawl.")
    parser.add_argument("--no-rich", action="store_true", help="Disable Rich dashboard even when stdout is a TTY.")
    return parser.parse_args()


def build_known_urls(args: argparse.Namespace) -> dict[str, str]:
    data = {
        Platform.TRIP.value: args.trip_url.strip(),
        Platform.BOOKING.value: args.booking_url.strip(),
        Platform.GOOGLE_HOTELS.value: args.google_url.strip(),
        Platform.AGODA.value: args.agoda_url.strip(),
    }
    valid: dict[str, str] = {}
    for platform, raw in data.items():
        if not raw:
            continue
        parsed = urlparse(raw)
        if parsed.scheme in {"http", "https"} and parsed.netloc:
            valid[platform] = raw
            continue
        print(
            f"warning: ignored invalid --{platform.replace('_hotels', '')}-url value: {raw}",
            file=sys.stderr,
        )
    return valid


def parse_platforms_csv(raw: str) -> list[Platform]:
    return [Platform(item.strip()) for item in raw.split(",") if item.strip()]


def build_single_request(args: argparse.Namespace) -> LiveQuoteRequest:
    missing: list[str] = []
    if not args.hotel.strip():
        missing.append("--hotel")
    if not args.checkin.strip():
        missing.append("--checkin")
    if not args.checkout.strip():
        missing.append("--checkout")
    if missing:
        raise SystemExit(
            "error: the following arguments are required in single-request mode: "
            + ", ".join(missing)
        )

    requested_platforms = parse_platforms_csv(args.platforms)
    return LiveQuoteRequest(
        hotel_name=args.hotel,
        city=args.city,
        checkin=date.fromisoformat(args.checkin),
        checkout=date.fromisoformat(args.checkout),
        adults=args.adults,
        children=args.children,
        currency=args.currency,
        platforms=requested_platforms,
        known_urls=build_known_urls(args),
        probe_days_ahead=args.probe_days,
        probe_step_days=args.probe_step,
    )


def load_batch_request(path_str: str, *, default_concurrency: int) -> BatchQuoteRequest:
    path = Path(path_str).expanduser()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"error: batch config file not found: {path}") from exc
    except json.JSONDecodeError as exc:
        raise SystemExit(f"error: invalid JSON in batch config {path}: {exc}") from exc

    if isinstance(raw, list):
        payload: dict[str, object] = {
            "hotels": raw,
            "concurrency": default_concurrency,
        }
        return BatchQuoteRequest.model_validate(payload)

    if not isinstance(raw, dict):
        raise SystemExit(f"error: batch config must be a JSON object or list: {path}")

    payload = dict(raw)
    if "requests" in payload and "hotels" not in payload:
        payload["hotels"] = payload.pop("requests")
    if not payload.get("concurrency"):
        payload["concurrency"] = default_concurrency

    return BatchQuoteRequest.model_validate(payload)


def iter_requested_platforms(
    single_request: LiveQuoteRequest | None,
    batch_request: BatchQuoteRequest | None,
) -> list[Platform]:
    ordered: list[Platform] = []

    def add(platform: Platform) -> None:
        if platform not in ordered:
            ordered.append(platform)

    if single_request is not None:
        for platform in single_request.platforms or []:
            add(platform)

    if batch_request is not None:
        for hotel_req in batch_request.hotels:
            for platform in hotel_req.platforms or []:
                add(platform)

    return ordered


def default_platform_profile(platform: Platform) -> str:
    folder_by_platform = {
        Platform.TRIP: "trip-profile",
        Platform.BOOKING: "booking-profile",
        Platform.AGODA: "agoda-profile",
    }
    return str((ROOT / ".playwright" / folder_by_platform[platform]).resolve())


def configure_playwright_driver(
    args: argparse.Namespace,
    requested_platforms: list[Platform],
) -> None:
    if os.environ.get("PLAYWRIGHT_NODEJS_PATH"):
        return
    should_use_system_node = (
        args.use_system_playwright_node
        or Platform.GOOGLE_HOTELS in requested_platforms
    )
    if not should_use_system_node:
        return

    node_path = shutil.which("node")
    if not node_path:
        if args.use_system_playwright_node:
            raise SystemExit("error: `node` was not found in PATH for --use-system-playwright-node")
        print(
            "warning: google_hotels requested but `node` was not found in PATH; using bundled Playwright node",
            file=sys.stderr,
        )
        return

    os.environ["PLAYWRIGHT_NODEJS_PATH"] = node_path


def build_runtime_settings(args: argparse.Namespace, requested_platforms: list[Platform]):
    base_settings = get_settings()
    updates = {"demo_mode": bool(args.demo_mode)}
    google_requested = Platform.GOOGLE_HOTELS in requested_platforms
    if google_requested and args.enable_google:
        updates["google_skip"] = False

    if base_settings.playwright_user_data_dir.strip():
        field_by_platform = {
            Platform.TRIP: "playwright_user_data_dir_trip",
            Platform.BOOKING: "playwright_user_data_dir_booking",
            Platform.AGODA: "playwright_user_data_dir_agoda",
        }
        for platform in requested_platforms:
            if platform == Platform.GOOGLE_HOTELS:
                continue
            field_name = field_by_platform[platform]
            if getattr(base_settings, field_name).strip():
                continue
            updates[field_name] = default_platform_profile(platform)

    settings = base_settings.model_copy(update=updates)
    if google_requested and settings.google_skip and not settings.demo_mode:
        print(
            "warning: google_hotels requested but GOOGLE_SKIP=true; pass --enable-google to force live crawl",
            file=sys.stderr,
        )
    return settings


def print_batch_summary(payload: dict) -> None:
    print("\nBATCH SUMMARY")
    results = payload.get("results", [])
    for index, item in enumerate(results, start=1):
        request = item.get("request", {})
        hotel_name = request.get("hotel_name", "")
        checkin = request.get("checkin", "")
        checkout = request.get("checkout", "")
        platform_results = item.get("results", [])
        parts: list[str] = []
        for result in platform_results:
            platform = result.get("platform", "?")
            structured = result.get("structured_offer_count", 0)
            offers = result.get("offer_count", 0)
            diagnosis = result.get("diagnosis", "")
            parts.append(f"{platform}={structured}/{offers} {diagnosis}")
        summary = " | ".join(parts) if parts else "no platform results"
        print(f"{index}. {hotel_name} {checkin}->{checkout} :: {summary}")

    errors = payload.get("errors", [])
    if errors:
        print("\nBATCH ERRORS")
        for error in errors:
            print(json.dumps(error, ensure_ascii=False))


async def main() -> None:
    args = parse_args()
    batch_mode = bool(args.batch_config.strip())
    req = None if batch_mode else build_single_request(args)
    batch_req = load_batch_request(args.batch_config, default_concurrency=args.batch_concurrency) if batch_mode else None

    requested_platforms = iter_requested_platforms(req, batch_req)
    configure_playwright_driver(args, requested_platforms)
    settings = build_runtime_settings(args, requested_platforms)
    service = LiveQuoteService(settings)

    use_rich = (
        not batch_mode
        and RICH_AVAILABLE
        and (args.rich or (not args.no_rich and sys.stdout.isatty()))
    )
    if batch_mode and args.rich:
        print("warning: --rich is not supported with --batch-config; falling back to plain output", file=sys.stderr)

    if use_rich and req is not None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "crawl_dashboard",
            ROOT / "scripts" / "crawl_dashboard.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        CrawlDashboard = mod.CrawlDashboard
        platform_order = [p.value for p in (req.platforms or requested_platforms)]
        request_summary = {
            "hotel_name": req.hotel_name,
            "checkin": req.checkin.isoformat(),
            "checkout": req.checkout.isoformat(),
            "currency": req.currency,
        }
        dashboard = CrawlDashboard(platform_order, request_summary)
        with Live(
            dashboard._render(),
            console=dashboard.console,
            refresh_per_second=2,
            transient=False,
        ) as live:
            dashboard._live = live
            resp = await service.quote(req, on_progress=dashboard.on_progress)
        payload = resp.model_dump(mode="json")
        dashboard.print_final(payload)
    elif batch_mode and batch_req is not None:
        resp = await service.batch_quote(batch_req)
        payload = resp.model_dump(mode="json")
        print_batch_summary(payload)
    else:
        assert req is not None
        resp = await service.quote(req)
        payload = resp.model_dump(mode="json")

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        if use_rich:
            dashboard.console.print(f"[dim]saved report:[/] [cyan]{out_path}[/]")
        else:
            print(f"saved report: {out_path}")

    if not use_rich:
        print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    asyncio.run(main())

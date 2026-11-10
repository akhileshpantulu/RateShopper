from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from playwright.async_api import async_playwright

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from hotelrate.config import get_settings


def parse_args() -> argparse.Namespace:
    settings = get_settings()
    parser = argparse.ArgumentParser(
        description="Open Trip with persistent Playwright profile and keep it open for manual login."
    )
    parser.add_argument(
        "--user-data-dir",
        default=settings.playwright_user_data_dir_trip or settings.playwright_user_data_dir or str(ROOT / ".playwright" / "trip-profile"),
    )
    parser.add_argument(
        "--url",
        default=(
            "https://www.trip.com/hotels/detail/?cityEnName=Shanghai&cityId=2&hotelId=114516981"
            "&checkIn=2026-03-02&checkOut=2026-03-03&adult=2&children=0&curr=USD&barcurr=USD"
        ),
    )
    parser.add_argument("--channel", default=settings.playwright_browser_channel)
    parser.add_argument("--wait-seconds", type=int, default=240, help="How long to keep browser open.")
    return parser.parse_args()


async def main() -> None:
    args = parse_args()
    settings = get_settings()
    user_data_dir = Path(args.user_data_dir).expanduser().resolve()
    user_data_dir.mkdir(parents=True, exist_ok=True)

    launch_kwargs: dict[str, object] = {
        "headless": False,
        "locale": settings.default_locale,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if args.channel and args.channel != "chromium":
        launch_kwargs["channel"] = args.channel

    print(f"[trip-login] user_data_dir: {user_data_dir}")
    print(f"[trip-login] open url: {args.url}")
    print("[trip-login] Please complete Trip login in the opened browser window.")
    print(
        "[trip-login] Keep the page on a hotel detail page with rates visible, "
        "then close the window or wait for timeout."
    )

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(str(user_data_dir), **launch_kwargs)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(args.url, wait_until="domcontentloaded", timeout=120000)
        closed_event = asyncio.Event()

        def on_close() -> None:
            closed_event.set()

        context.on("close", on_close)
        try:
            await asyncio.wait_for(closed_event.wait(), timeout=args.wait_seconds)
            print("[trip-login] browser closed by user, profile should be saved.")
        except asyncio.TimeoutError:
            print("[trip-login] timeout reached, closing browser context.")
        finally:
            try:
                await context.close()
            except Exception:
                pass

    print("[trip-login] profile warmup finished.")


if __name__ == "__main__":
    asyncio.run(main())

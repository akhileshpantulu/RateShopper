from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from playwright.async_api import async_playwright

from hotelrate.config import get_settings


DEFAULT_URLS = {
    "trip": "https://www.trip.com/account/signin",
    "booking": "https://account.booking.com/sign-in",
    "agoda": "https://www.agoda.com/",
    "google_hotels": "https://www.google.com/travel/search",
}


def parse_args() -> argparse.Namespace:
    settings = get_settings()
    parser = argparse.ArgumentParser(description="Warm up persistent browser profile for a platform login.")
    parser.add_argument("--platform", required=True, choices=sorted(DEFAULT_URLS.keys()))
    parser.add_argument("--user-data-dir", default="")
    parser.add_argument("--url", default="")
    parser.add_argument("--wait-seconds", type=int, default=300)
    parser.add_argument("--channel", default=settings.playwright_browser_channel)
    return parser.parse_args()


def _default_profile_dir(platform: str) -> str:
    settings = get_settings()
    platform_dirs = {
        "trip": settings.playwright_user_data_dir_trip.strip(),
        "booking": settings.playwright_user_data_dir_booking.strip(),
        "agoda": settings.playwright_user_data_dir_agoda.strip(),
        "google_hotels": settings.playwright_user_data_dir_google_hotels.strip(),
    }
    if platform_dirs.get(platform):
        return platform_dirs[platform]
    if settings.playwright_user_data_dir.strip():
        return settings.playwright_user_data_dir.strip()
    return str(ROOT / ".playwright" / f"{platform}-profile")


async def main() -> None:
    args = parse_args()
    settings = get_settings()
    user_data_dir = args.user_data_dir or _default_profile_dir(args.platform)
    url = args.url or DEFAULT_URLS[args.platform]
    profile = Path(user_data_dir).expanduser().resolve()
    profile.mkdir(parents=True, exist_ok=True)

    launch_kwargs: dict[str, object] = {
        "headless": False,
        "locale": settings.default_locale,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    if args.channel and args.channel != "chromium":
        launch_kwargs["channel"] = args.channel

    print(f"[warmup] platform={args.platform}")
    print(f"[warmup] user_data_dir={profile}")
    print(f"[warmup] open url={url}")
    print("[warmup] Complete login manually, then close browser or wait for timeout.")

    async with async_playwright() as pw:
        context = await pw.chromium.launch_persistent_context(str(profile), **launch_kwargs)
        page = context.pages[0] if context.pages else await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=120000)
        closed_event = asyncio.Event()
        context.on("close", lambda: closed_event.set())
        try:
            await asyncio.wait_for(closed_event.wait(), timeout=args.wait_seconds)
            print("[warmup] browser closed by user.")
        except asyncio.TimeoutError:
            print("[warmup] timeout reached.")
        finally:
            try:
                await context.close()
            except Exception:
                pass

    print("[warmup] done.")


if __name__ == "__main__":
    asyncio.run(main())

"""Browser login warmup — extracted from ``scripts/platform_login_warmup.py``.

Opens a headed (visible) persistent browser context so the user can complete
a manual login.  The session cookies are stored in the profile directory for
later headless reuse by the collectors.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from pathlib import Path

from hotelrate.config import Settings

# ── Platform metadata ─────────────────────────────────────────────────

PLATFORM_LOGIN_URLS: dict[str, str] = {
    "trip": "https://www.trip.com/account/signin",
    "booking": "https://account.booking.com/sign-in",
    "agoda": "https://www.agoda.com/",
}

PLATFORM_LABELS: dict[str, str] = {
    "trip": "Trip.com",
    "booking": "Booking.com",
    "agoda": "Agoda",
}

# All three require manual login for full functionality.
PLATFORM_LOGIN_REQUIRED: list[str] = ["trip", "booking", "agoda"]


def resolve_profile_dir(platform: str, settings: Settings) -> Path:
    """Unified 3-tier profile directory resolution.

    Resolution order:
    1. Per-platform setting (e.g. ``playwright_user_data_dir_trip``)
    2. Global ``playwright_user_data_dir``
    3. Default: ``<cwd>/.playwright/<platform>-profile``
    """
    platform_dirs = {
        "trip": settings.playwright_user_data_dir_trip.strip(),
        "booking": settings.playwright_user_data_dir_booking.strip(),
        "agoda": settings.playwright_user_data_dir_agoda.strip(),
        "google_hotels": settings.playwright_user_data_dir_google_hotels.strip(),
    }
    value = platform_dirs.get(platform, "")
    if value:
        return Path(value).expanduser().resolve()
    if settings.playwright_user_data_dir.strip():
        return Path(settings.playwright_user_data_dir.strip()).expanduser().resolve()
    return (Path.cwd() / ".playwright" / f"{platform}-profile").resolve()


async def open_login_browser(
    platform: str,
    settings: Settings,
    *,
    url: str = "",
    timeout_seconds: int = 300,
    on_status: Callable[[str], None] | None = None,
) -> bool:
    """Open a headed browser for manual login, wait for close or timeout.

    Parameters
    ----------
    platform:
        One of ``"trip"``, ``"booking"``, ``"agoda"``.
    settings:
        Application settings (used for browser channel, locale).
    url:
        Override the default login URL.
    timeout_seconds:
        How long to wait before auto-closing.
    on_status:
        Optional callback for status messages (e.g. to display in the CLI).

    Returns
    -------
    bool
        ``True`` if the browser was closed normally (user finished),
        ``False`` on timeout or error.
    """
    from playwright.async_api import async_playwright

    target_url = url or PLATFORM_LOGIN_URLS.get(platform, "")
    profile = resolve_profile_dir(platform, settings)
    profile.mkdir(parents=True, exist_ok=True)

    launch_kwargs: dict[str, object] = {
        "headless": False,
        "locale": settings.default_locale,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    channel = settings.playwright_browser_channel.strip().lower()
    if channel and channel != "chromium":
        launch_kwargs["channel"] = channel

    def _emit(msg: str) -> None:
        if on_status:
            on_status(msg)

    _emit(f"Opening browser for {PLATFORM_LABELS.get(platform, platform)}...")
    _emit(f"Profile: {profile}")
    _emit(f"URL: {target_url}")
    _emit("Complete login manually, then close the browser window.")

    try:
        async with async_playwright() as pw:
            context = await pw.chromium.launch_persistent_context(
                str(profile), **launch_kwargs
            )
            page = context.pages[0] if context.pages else await context.new_page()
            await page.goto(target_url, wait_until="domcontentloaded", timeout=120000)

            closed_event = asyncio.Event()
            context.on("close", lambda: closed_event.set())

            try:
                await asyncio.wait_for(closed_event.wait(), timeout=timeout_seconds)
                _emit("Browser closed by user.")
                return True
            except asyncio.TimeoutError:
                _emit(f"Timeout ({timeout_seconds}s) reached.")
                return False
            finally:
                try:
                    await context.close()
                except Exception:
                    pass
    except Exception as exc:
        _emit(f"Error: {exc}")
        return False

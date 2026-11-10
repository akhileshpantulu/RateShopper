"""Diagnostic check functions for ``hotelrate-mcp doctor`` and ``setup``.

Each check function returns a :class:`CheckResult` with a pass/warn/error
level.  Synchronous checks inspect the local filesystem and importable
packages; asynchronous *probe* checks launch a lightweight headless browser
to verify that a platform session is alive.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from hotelrate.config import Settings

# Where ``playwright install chromium`` puts its binary (macOS / Linux default).
_CHROMIUM_CACHE = Path.home() / ".cache" / "ms-playwright"

# Project root (best-effort: walk up from this file).
_PROJECT_ROOT = Path.cwd()

# Platforms that require manual login for session persistence.
PLATFORMS_LOGIN_REQUIRED: list[str] = ["trip", "booking", "agoda"]
ALL_PLATFORMS: list[str] = ["trip", "booking", "agoda", "google_hotels"]


@dataclass
class CheckResult:
    """Outcome of a single diagnostic check."""

    name: str
    label: str
    level: str  # "ok" | "warning" | "error" | "skip"
    message: str
    fix_command: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Synchronous checks
# ---------------------------------------------------------------------------


def check_python_version() -> CheckResult:
    """Python >= 3.12 is required."""
    v = sys.version_info
    version_str = f"Python {v.major}.{v.minor}.{v.micro}"
    if v >= (3, 12):
        return CheckResult(
            name="python_version",
            label="Python ≥3.12",
            level="ok",
            message=version_str,
        )
    return CheckResult(
        name="python_version",
        label="Python ≥3.12",
        level="error",
        message=f"{version_str} — requires 3.12+",
    )


def check_playwright_package() -> CheckResult:
    """Check whether the ``playwright`` Python package is importable."""
    if importlib.util.find_spec("playwright") is not None:
        return CheckResult(
            name="playwright_package",
            label="Playwright package",
            level="ok",
            message="installed",
        )
    return CheckResult(
        name="playwright_package",
        label="Playwright package",
        level="error",
        message="not installed",
        fix_command="pip install playwright",
    )


def check_browser_binary(settings: Settings) -> CheckResult:
    """Check whether the Playwright-managed browser binary is on disk."""
    channel = settings.playwright_browser_channel.strip().lower()
    target = channel or "chromium"

    # Non-chromium channel (e.g. "chrome") — assumed installed by user.
    if target != "chromium":
        return CheckResult(
            name="browser_binary",
            label="Browser binary",
            level="ok",
            message=f"channel={target} (system)",
        )

    # Playwright not installed → skip (the package check already covers it).
    if importlib.util.find_spec("playwright") is None:
        return CheckResult(
            name="browser_binary",
            label="Chromium browser",
            level="skip",
            message="playwright not installed",
        )

    found = False
    if _CHROMIUM_CACHE.is_dir():
        found = any(_CHROMIUM_CACHE.glob("chromium-*"))

    if found:
        return CheckResult(
            name="browser_binary",
            label="Chromium browser",
            level="ok",
            message="found",
        )
    return CheckResult(
        name="browser_binary",
        label="Chromium browser",
        level="error",
        message=f"not found in {_CHROMIUM_CACHE}",
        fix_command="playwright install chromium",
    )


def check_dotenv() -> CheckResult:
    """Check whether a ``.env`` file exists in the working directory."""
    if Path(".env").exists():
        return CheckResult(
            name="dotenv",
            label=".env file",
            level="ok",
            message="exists",
        )
    return CheckResult(
        name="dotenv",
        label=".env file",
        level="warning",
        message="not found — using defaults (DEMO_MODE=true)",
        fix_command="cp .env.example .env",
    )


def check_dotenv_settings(settings: Settings) -> CheckResult:
    """Report key toggles (demo_mode, headless) so the user knows the config."""
    parts: list[str] = []
    level = "ok"
    if settings.demo_mode:
        parts.append("DEMO_MODE=true")
        level = "warning"
    else:
        parts.append("DEMO_MODE=false")
    parts.append(f"HEADLESS={'true' if settings.headless else 'false'}")
    message = ", ".join(parts)
    fix = ""
    if settings.demo_mode:
        message += " (set to false for live crawls)"
        fix = "edit .env → DEMO_MODE=false"
    return CheckResult(
        name="dotenv_settings",
        label="Settings",
        level=level,
        message=message,
        fix_command=fix,
    )


def _resolve_profile_dir(platform: str, settings: Settings) -> Path:
    """Unified 3-tier profile directory resolution.

    1. Per-platform env var (e.g. ``PLAYWRIGHT_USER_DATA_DIR_TRIP``)
    2. Global ``PLAYWRIGHT_USER_DATA_DIR``
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
    return (_PROJECT_ROOT / ".playwright" / f"{platform}-profile").resolve()


def check_platform_profile(platform: str, settings: Settings) -> CheckResult:
    """Check whether a platform's browser profile directory exists and has data."""
    profile = _resolve_profile_dir(platform, settings)
    label = f"{platform.replace('_', ' ').title()} profile"

    if not profile.exists():
        if platform in PLATFORMS_LOGIN_REQUIRED:
            return CheckResult(
                name=f"profile_{platform}",
                label=label,
                level="warning",
                message="not found",
                fix_command=f"hotelrate-mcp setup --platforms {platform}",
                metadata={"path": str(profile)},
            )
        return CheckResult(
            name=f"profile_{platform}",
            label=label,
            level="skip",
            message="not configured",
            metadata={"path": str(profile)},
        )

    file_count = sum(1 for _ in profile.rglob("*") if _.is_file())
    return CheckResult(
        name=f"profile_{platform}",
        label=label,
        level="ok",
        message=f"{profile.relative_to(Path.cwd()) if profile.is_relative_to(Path.cwd()) else profile} ({file_count} files)",
        metadata={"path": str(profile), "file_count": file_count},
    )


# ---------------------------------------------------------------------------
# Aggregate runners
# ---------------------------------------------------------------------------


def run_all_checks(settings: Settings) -> list[CheckResult]:
    """Run all synchronous diagnostic checks."""
    results: list[CheckResult] = [
        check_python_version(),
        check_playwright_package(),
        check_browser_binary(settings),
        check_dotenv(),
        check_dotenv_settings(settings),
    ]
    for platform in PLATFORMS_LOGIN_REQUIRED:
        results.append(check_platform_profile(platform, settings))
    return results


# ---------------------------------------------------------------------------
# Async probe checks (for --probe / setup step 6)
# ---------------------------------------------------------------------------


async def probe_platform_session(platform: str, settings: Settings) -> CheckResult:
    """Lightweight headless probe: open persistent context → load homepage → check redirect.

    Returns *ok* if the page loads without redirecting to a login page, *error*
    otherwise.  This is intentionally cheap (no collector logic, no price
    extraction) so it's safe for diagnostic use.
    """
    from hotelrate.apps.mcp.cli.warmup import PLATFORM_LOGIN_URLS

    label = f"{platform.replace('_', ' ').title()} session"
    profile = _resolve_profile_dir(platform, settings)

    if not profile.exists():
        return CheckResult(
            name=f"probe_{platform}",
            label=label,
            level="error",
            message="profile directory does not exist",
            fix_command=f"hotelrate-mcp setup --platforms {platform}",
        )

    url = PLATFORM_LOGIN_URLS.get(platform, "")
    if not url:
        return CheckResult(
            name=f"probe_{platform}",
            label=label,
            level="skip",
            message="no probe URL configured",
        )

    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return CheckResult(
            name=f"probe_{platform}",
            label=label,
            level="skip",
            message="playwright not installed",
        )

    launch_kwargs: dict[str, object] = {
        "headless": True,
        "args": ["--disable-blink-features=AutomationControlled"],
    }
    channel = settings.playwright_browser_channel.strip().lower()
    if channel and channel != "chromium":
        launch_kwargs["channel"] = channel

    try:
        async with async_playwright() as pw:
            ctx = await pw.chromium.launch_persistent_context(str(profile), **launch_kwargs)
            page = ctx.pages[0] if ctx.pages else await ctx.new_page()
            response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            final_url = page.url
            status = response.status if response else 0
            await ctx.close()

        # Heuristic: if we ended up on a sign-in / login page the session is dead.
        login_indicators = ["sign-in", "signin", "login", "account/"]
        landed_on_login = any(ind in final_url.lower() for ind in login_indicators)

        if landed_on_login and status < 400:
            return CheckResult(
                name=f"probe_{platform}",
                label=label,
                level="error",
                message=f"redirected to login ({final_url})",
                fix_command=f"hotelrate-mcp setup --platforms {platform}",
            )

        return CheckResult(
            name=f"probe_{platform}",
            label=label,
            level="ok",
            message=f"session alive (HTTP {status})",
        )

    except Exception as exc:
        return CheckResult(
            name=f"probe_{platform}",
            label=label,
            level="error",
            message=f"probe failed: {exc}",
        )


async def run_probe_checks(
    settings: Settings, platforms: list[str] | None = None
) -> list[CheckResult]:
    """Run async session probes for the given platforms (default: login-required ones)."""
    targets = platforms or PLATFORMS_LOGIN_REQUIRED
    results: list[CheckResult] = []
    for p in targets:
        results.append(await probe_platform_session(p, settings))
    return results

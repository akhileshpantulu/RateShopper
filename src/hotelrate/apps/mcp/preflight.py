"""Environment preflight checks for the MCP server.

Runs at startup to detect common configuration issues (missing Playwright,
missing browser binaries, absent .env, etc.) and surfaces them as structured
issue dicts so the AI can guide users to fix problems *before* a live crawl
fails deep inside a collector.

Design principles:
- Check only, never auto-install (stdout is reserved for MCP protocol).
- Demo mode is lenient (info); live mode is strict (error).
- Never block startup — always return the issue list and let the server run.
"""

from __future__ import annotations

import importlib.util
import logging
from pathlib import Path
from typing import Any

from hotelrate.config import Settings

logger = logging.getLogger(__name__)

# Glob patterns where ``playwright install chromium`` puts its binary.
# macOS / Linux default: ~/.cache/ms-playwright/chromium-*/
_CHROMIUM_CACHE = Path.home() / ".cache" / "ms-playwright"


def _browser_install_target(settings: Settings) -> str:
    channel = settings.playwright_browser_channel.strip().lower()
    return channel or "chromium"


def _browser_install_command(settings: Settings) -> str:
    return f"playwright install {_browser_install_target(settings)}"


def _check_playwright_package(settings: Settings) -> dict[str, Any] | None:
    """Check whether the ``playwright`` Python package is importable."""
    if importlib.util.find_spec("playwright") is not None:
        return None

    level = "info" if settings.demo_mode else "error"
    return {
        "check": "playwright_package",
        "level": level,
        "message": (
            "Playwright Python package is not installed. "
            "Live mode requires it to launch browsers."
        ),
        "fix_command": f"pip install playwright && {_browser_install_command(settings)}",
    }


def _check_chromium_binary(settings: Settings) -> dict[str, Any] | None:
    """Check whether a Chromium binary installed by Playwright exists on disk."""
    # If playwright package itself is missing, skip the binary check —
    # the package check already covers that.
    if importlib.util.find_spec("playwright") is None:
        return None
    if _browser_install_target(settings) != "chromium":
        return None

    found = False
    if _CHROMIUM_CACHE.is_dir():
        found = any(_CHROMIUM_CACHE.glob("chromium-*"))

    if found:
        return None

    level = "info" if settings.demo_mode else "error"
    return {
        "check": "chromium_binary",
        "level": level,
        "message": (
            "Chromium browser binary not found in "
            f"{_CHROMIUM_CACHE}. "
            "Live mode needs it for Playwright crawls."
        ),
        "fix_command": _browser_install_command(settings),
    }


def _check_dotenv() -> dict[str, Any] | None:
    """Check whether a ``.env`` file exists in the working directory."""
    if Path(".env").exists():
        return None
    return {
        "check": "dotenv",
        "level": "info",
        "message": (
            ".env file not found in the current directory. "
            "The server will use built-in defaults (demo_mode=true). "
            "Copy the example to customise."
        ),
        "fix_command": "cp .env.example .env",
    }


def check_environment(settings: Settings) -> list[dict[str, Any]]:
    """Run all preflight checks and return a list of issue dicts.

    Each dict contains:
    - ``check``: short identifier (e.g. ``"playwright_package"``)
    - ``level``: ``"error"`` | ``"warning"`` | ``"info"``
    - ``message``: human-readable description of the problem
    - ``fix_command``: shell command the user can run to resolve it

    An empty list means no issues were detected.
    """
    issues: list[dict[str, Any]] = []

    for check_fn in (
        lambda: _check_playwright_package(settings),
        lambda: _check_chromium_binary(settings),
        _check_dotenv,
    ):
        issue = check_fn()
        if issue is not None:
            issues.append(issue)

    return issues

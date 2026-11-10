"""Collection policy for browser and HTTP identity.

By default the crawler identifies itself honestly: the browser keeps its real
user agent and automation markers, and plain HTTP requests carry a fixed
``hotelrate-mcp`` user agent. Setting ``STEALTH_MODE=true`` restores the upstream
behaviour (masked automation markers, rotating desktop user agents). That switch
is off by default because masking conflicts with this project's collection rules
(docs/DESIGN.md, section 4) and may breach the terms of use of the sites crawled.
"""

from __future__ import annotations

from typing import Any

from hotelrate import __version__
from hotelrate.config import Settings

AUTOMATION_MASK_ARG = "--disable-blink-features=AutomationControlled"
WEBDRIVER_MASK_SCRIPT = "Object.defineProperty(navigator, 'webdriver', {get: () => undefined});"
HONEST_HTTP_USER_AGENT = f"hotelrate-mcp/{__version__}"


def masks_automation(settings: Settings) -> bool:
    """True when the browser should hide that it is automated."""
    return settings.stealth_mode and settings.playwright_disable_automation_flags


def automation_mask_args(settings: Settings) -> list[str]:
    """Chromium launch args that hide automation; empty unless stealth is on."""
    return [AUTOMATION_MASK_ARG] if masks_automation(settings) else []


async def apply_stealth_scripts(context: Any, settings: Settings) -> None:
    """Hide ``navigator.webdriver`` on *context*; a no-op unless stealth is on."""
    if settings.stealth_mode:
        await context.add_init_script(WEBDRIVER_MASK_SCRIPT)


def http_user_agent(settings: Settings, stealth_user_agent: str) -> str:
    """User agent for plain HTTP requests."""
    return stealth_user_agent if settings.stealth_mode else HONEST_HTTP_USER_AGENT

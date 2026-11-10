"""Collection policy: honest browser identity by default, stealth only on request."""

from __future__ import annotations

from typing import Any

import pytest

from hotelrate.collectors import GoogleHotelsCollector
from hotelrate.config import Settings
from hotelrate.core.browser_policy import (
    AUTOMATION_MASK_ARG,
    HONEST_HTTP_USER_AGENT,
    WEBDRIVER_MASK_SCRIPT,
    automation_mask_args,
)
from hotelrate.schemas import LiveQuoteRequest
from hotelrate.services.url_discovery import UrlDiscoveryService


class _FakeContext:
    def __init__(self) -> None:
        self.init_scripts: list[str] = []

    async def add_init_script(self, script: str) -> None:
        self.init_scripts.append(script)


class _FakeBrowser:
    def __init__(self) -> None:
        self.context_kwargs: dict[str, Any] = {}
        self.context = _FakeContext()

    async def new_context(self, **kwargs: Any) -> _FakeContext:
        self.context_kwargs = kwargs
        return self.context


class _FakeChromium:
    def __init__(self) -> None:
        self.launch_kwargs: dict[str, Any] = {}
        self.browser = _FakeBrowser()

    async def launch(self, **kwargs: Any) -> _FakeBrowser:
        self.launch_kwargs = kwargs
        return self.browser


class _FakePlaywright:
    def __init__(self) -> None:
        self.chromium = _FakeChromium()


def _settings(**overrides: Any) -> Settings:
    return Settings(_env_file=None, **overrides)


async def _open_context(settings: Settings) -> _FakePlaywright:
    # Google Hotels uses a non-persistent context unless a profile is configured,
    # so this exercises the plain launch path without touching the filesystem.
    pw = _FakePlaywright()
    await GoogleHotelsCollector(settings).open_playwright_context(pw)  # type: ignore[arg-type]
    return pw


def test_stealth_is_off_by_default() -> None:
    assert _settings().stealth_mode is False


async def test_default_browser_context_is_not_masked() -> None:
    pw = await _open_context(_settings())

    assert AUTOMATION_MASK_ARG not in pw.chromium.launch_kwargs["args"]
    assert "user_agent" not in pw.chromium.browser.context_kwargs
    assert pw.chromium.browser.context.init_scripts == []


async def test_stealth_mode_restores_upstream_masking() -> None:
    pw = await _open_context(_settings(stealth_mode=True))

    assert AUTOMATION_MASK_ARG in pw.chromium.launch_kwargs["args"]
    assert pw.chromium.browser.context_kwargs["user_agent"] in GoogleHotelsCollector.USER_AGENTS
    assert pw.chromium.browser.context.init_scripts == [WEBDRIVER_MASK_SCRIPT]


@pytest.mark.parametrize(
    ("stealth_mode", "disable_flags", "expected"),
    [
        (False, True, []),
        (False, False, []),
        (True, False, []),
        (True, True, [AUTOMATION_MASK_ARG]),
    ],
)
def test_automation_mask_args_need_stealth(
    stealth_mode: bool, disable_flags: bool, expected: list[str]
) -> None:
    settings = _settings(
        stealth_mode=stealth_mode, playwright_disable_automation_flags=disable_flags
    )
    assert automation_mask_args(settings) == expected


def test_search_requests_use_honest_user_agent_by_default() -> None:
    assert UrlDiscoveryService(_settings())._http_user_agent() == HONEST_HTTP_USER_AGENT
    assert (
        UrlDiscoveryService(_settings(stealth_mode=True))._http_user_agent()
        == UrlDiscoveryService.SEARCH_USER_AGENT
    )


def test_quotes_are_for_the_requested_dates_by_default() -> None:
    request = LiveQuoteRequest(hotel_name="Example Hotel", checkin="2027-03-01", checkout="2027-03-02")
    assert request.probe_days_ahead == 0

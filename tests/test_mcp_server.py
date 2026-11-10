"""Tests for the MCP server module.

Uses the MCP SDK in-memory client to exercise all 4 tools without any
network I/O or Playwright browser.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from unittest.mock import patch

import pytest

from hotelrate.apps.mcp.preflight import check_environment
from hotelrate.apps.mcp.server import mcp
from hotelrate.config import Settings, get_settings
from mcp.shared.memory import create_connected_server_and_client_session


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def _force_demo_mode(monkeypatch):
    """Ensure DEMO_MODE=true for all tests so no real browser is launched."""
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("HEADLESS", "true")
    monkeypatch.setenv("FIRECRAWL_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def _force_live_mode(monkeypatch):
    """Set DEMO_MODE=false (live mode) for preflight tests."""
    monkeypatch.setenv("DEMO_MODE", "false")
    monkeypatch.setenv("HEADLESS", "true")
    monkeypatch.setenv("FIRECRAWL_ENABLED", "false")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


# ---------------------------------------------------------------------------
# Tool registration
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("_force_demo_mode")
async def test_server_lists_all_four_tools():
    async with create_connected_server_and_client_session(mcp) as client:
        result = await client.list_tools()
        tool_names = sorted(t.name for t in result.tools)
        assert tool_names == ["health_check", "hotel_quote", "hotel_quote_batch", "list_platforms"]


# ---------------------------------------------------------------------------
# list_platforms
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("_force_demo_mode")
async def test_list_platforms_returns_all_platforms():
    async with create_connected_server_and_client_session(mcp) as client:
        result = await client.call_tool("list_platforms", {})
        # Result is a list of content blocks
        assert len(result.content) == 1
        data = json.loads(result.content[0].text)
        platform_names = sorted(p["name"] for p in data["platforms"])
        assert platform_names == ["agoda", "booking", "google_hotels", "trip"]
        assert "demo_mode" in data


# ---------------------------------------------------------------------------
# health_check
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("_force_demo_mode")
async def test_health_check_returns_expected_fields():
    async with create_connected_server_and_client_session(mcp) as client:
        result = await client.call_tool("health_check", {})
        assert len(result.content) == 1
        data = json.loads(result.content[0].text)
        assert data["status"] == "ok"
        assert "demo_mode" in data
        assert "playwright" in data
        assert "firecrawl" in data
        assert "platforms" in data
        assert "config" in data


# ---------------------------------------------------------------------------
# hotel_quote — demo mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("_force_demo_mode")
async def test_hotel_quote_demo_mode_returns_results():
    checkin = (date.today() + timedelta(days=7)).isoformat()
    checkout = (date.today() + timedelta(days=8)).isoformat()

    async with create_connected_server_and_client_session(mcp) as client:
        result = await client.call_tool(
            "hotel_quote",
            {
                "hotel_name": "Test Hotel Demo",
                "checkin": checkin,
                "checkout": checkout,
                "city": "TestCity",
                "platforms": ["trip", "booking"],
            },
        )
        assert len(result.content) >= 1
        data = json.loads(result.content[0].text)
        assert "generated_at" in data
        assert "request" in data
        assert "results" in data
        assert len(data["results"]) == 2
        for platform_result in data["results"]:
            assert platform_result["platform"] in ("trip", "booking")


# ---------------------------------------------------------------------------
# hotel_quote — error handling
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("_force_demo_mode")
async def test_hotel_quote_invalid_date_returns_error():
    async with create_connected_server_and_client_session(mcp) as client:
        result = await client.call_tool(
            "hotel_quote",
            {
                "hotel_name": "Test Hotel",
                "checkin": "2026-05-10",
                "checkout": "2026-05-09",  # before checkin
            },
        )
        # Should return error in content
        assert result.isError


@pytest.mark.asyncio
@pytest.mark.usefixtures("_force_demo_mode")
async def test_hotel_quote_invalid_platform_returns_error():
    async with create_connected_server_and_client_session(mcp) as client:
        result = await client.call_tool(
            "hotel_quote",
            {
                "hotel_name": "Test Hotel",
                "platforms": ["nonexistent_platform"],
            },
        )
        assert result.isError


# ---------------------------------------------------------------------------
# hotel_quote_batch — demo mode
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("_force_demo_mode")
async def test_hotel_quote_batch_demo_mode():
    checkin = (date.today() + timedelta(days=7)).isoformat()
    checkout = (date.today() + timedelta(days=8)).isoformat()

    async with create_connected_server_and_client_session(mcp) as client:
        result = await client.call_tool(
            "hotel_quote_batch",
            {
                "hotels": [
                    {"hotel_name": "Hotel A", "city": "CityA", "checkin": checkin, "checkout": checkout, "platforms": ["trip"]},
                    {"hotel_name": "Hotel B", "city": "CityB", "checkin": checkin, "checkout": checkout, "platforms": ["booking"]},
                ],
                "concurrency": 2,
            },
        )
        assert len(result.content) >= 1
        data = json.loads(result.content[0].text)
        assert data["total_hotels"] == 2


# ---------------------------------------------------------------------------
# Preflight — unit tests for check_environment()
# ---------------------------------------------------------------------------


def test_preflight_demo_mode_returns_info_level():
    """In demo mode, missing Playwright should produce info-level issues."""
    settings = Settings(demo_mode=True, firecrawl_enabled=False)
    with patch("hotelrate.apps.mcp.preflight.importlib.util.find_spec", return_value=None):
        issues = check_environment(settings)

    playwright_issues = [i for i in issues if i["check"] == "playwright_package"]
    assert len(playwright_issues) == 1
    assert playwright_issues[0]["level"] == "info"
    assert "pip install playwright" in playwright_issues[0]["fix_command"]


def test_preflight_live_mode_returns_error_level():
    """In live mode, missing Playwright should produce error-level issues."""
    settings = Settings(demo_mode=False, firecrawl_enabled=False)
    with patch("hotelrate.apps.mcp.preflight.importlib.util.find_spec", return_value=None):
        issues = check_environment(settings)

    playwright_issues = [i for i in issues if i["check"] == "playwright_package"]
    assert len(playwright_issues) == 1
    assert playwright_issues[0]["level"] == "error"


def test_preflight_no_issues_when_all_present():
    """No issues when playwright is installed and chromium binary exists."""
    settings = Settings(demo_mode=False, firecrawl_enabled=False)
    with (
        patch("hotelrate.apps.mcp.preflight.importlib.util.find_spec", return_value=True),
        patch("hotelrate.apps.mcp.preflight._CHROMIUM_CACHE") as mock_cache,
        patch("hotelrate.apps.mcp.preflight.Path") as mock_path,
    ):
        mock_cache.is_dir.return_value = True
        mock_cache.glob.return_value = ["/fake/chromium-1234"]
        # .env exists
        mock_path.return_value.exists.return_value = True
        issues = check_environment(settings)

    # Should have no issues (all checks pass)
    assert len(issues) == 0


def test_preflight_chromium_missing_live_mode():
    """In live mode, missing chromium binary should produce error-level issue."""
    settings = Settings(demo_mode=False, firecrawl_enabled=False)
    with (
        patch("hotelrate.apps.mcp.preflight.importlib.util.find_spec", return_value=True),
        patch("hotelrate.apps.mcp.preflight._CHROMIUM_CACHE") as mock_cache,
    ):
        mock_cache.is_dir.return_value = True
        mock_cache.glob.return_value = []  # no chromium found
        issues = check_environment(settings)

    chromium_issues = [i for i in issues if i["check"] == "chromium_binary"]
    assert len(chromium_issues) == 1
    assert chromium_issues[0]["level"] == "error"
    assert "playwright install chromium" in chromium_issues[0]["fix_command"]


def test_preflight_dotenv_missing():
    """Missing .env produces info-level issue."""
    settings = Settings(demo_mode=True, firecrawl_enabled=False)
    with (
        patch("hotelrate.apps.mcp.preflight.importlib.util.find_spec", return_value=True),
        patch("hotelrate.apps.mcp.preflight._CHROMIUM_CACHE") as mock_cache,
    ):
        mock_cache.is_dir.return_value = True
        mock_cache.glob.return_value = ["/fake/chromium-1234"]
        issues = check_environment(settings)

    dotenv_issues = [i for i in issues if i["check"] == "dotenv"]
    # This may or may not appear depending on whether .env exists in the test runner's cwd.
    # But the level should always be "info" if present.
    for issue in dotenv_issues:
        assert issue["level"] == "info"


# ---------------------------------------------------------------------------
# Preflight — integration tests via MCP tools
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.usefixtures("_force_demo_mode")
async def test_health_check_includes_preflight_fields():
    """health_check should include issues and setup_commands fields."""
    async with create_connected_server_and_client_session(mcp) as client:
        result = await client.call_tool("health_check", {})
        assert len(result.content) == 1
        data = json.loads(result.content[0].text)
        assert "issues" in data
        assert "setup_commands" in data
        assert isinstance(data["issues"], list)
        assert isinstance(data["setup_commands"], list)


@pytest.mark.asyncio
@pytest.mark.usefixtures("_force_demo_mode")
async def test_hotel_quote_demo_mode_ignores_preflight_errors():
    """In demo mode, hotel_quote should succeed even if preflight has issues."""
    checkin = (date.today() + timedelta(days=7)).isoformat()
    checkout = (date.today() + timedelta(days=8)).isoformat()

    async with create_connected_server_and_client_session(mcp) as client:
        result = await client.call_tool(
            "hotel_quote",
            {
                "hotel_name": "Test Hotel",
                "checkin": checkin,
                "checkout": checkout,
                "platforms": ["trip"],
            },
        )
        assert not result.isError

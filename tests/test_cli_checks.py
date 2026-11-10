"""Tests for ``hotelrate.apps.mcp.cli.checks``."""

from __future__ import annotations

import sys
from collections import namedtuple

import pytest

from hotelrate.apps.mcp.cli.checks import (
    CheckResult,
    _resolve_profile_dir,
    check_browser_binary,
    check_dotenv,
    check_dotenv_settings,
    check_platform_profile,
    check_playwright_package,
    check_python_version,
    run_all_checks,
)
from hotelrate.config import Settings, get_settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _make_settings(**overrides) -> Settings:
    defaults = {"demo_mode": True, "headless": True}
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


# ---------------------------------------------------------------------------
# check_python_version
# ---------------------------------------------------------------------------


def test_python_version_ok():
    result = check_python_version()
    # We're running under 3.12+ so should pass.
    assert result.level == "ok"
    assert "Python" in result.message


def test_python_version_too_old(monkeypatch):
    FakeVersionInfo = namedtuple("version_info", ["major", "minor", "micro", "releaselevel", "serial"])
    fake_info = FakeVersionInfo(3, 11, 0, "final", 0)
    monkeypatch.setattr(sys, "version_info", fake_info)
    result = check_python_version()
    assert result.level == "error"
    assert "3.12" in result.message


# ---------------------------------------------------------------------------
# check_playwright_package
# ---------------------------------------------------------------------------


def test_playwright_package_found():
    # playwright is installed in our dev environment
    result = check_playwright_package()
    assert result.level == "ok"
    assert result.message == "installed"


def test_playwright_package_missing(monkeypatch):
    monkeypatch.setattr("hotelrate.apps.mcp.cli.checks.importlib.util.find_spec", lambda _: None)
    result = check_playwright_package()
    assert result.level == "error"
    assert "not installed" in result.message
    assert result.fix_command == "pip install playwright"


# ---------------------------------------------------------------------------
# check_browser_binary
# ---------------------------------------------------------------------------


def test_browser_binary_non_chromium():
    settings = _make_settings(playwright_browser_channel="chrome")
    result = check_browser_binary(settings)
    assert result.level == "ok"
    assert "system" in result.message


def test_browser_binary_playwright_missing(monkeypatch):
    monkeypatch.setattr("hotelrate.apps.mcp.cli.checks.importlib.util.find_spec", lambda _: None)
    settings = _make_settings(playwright_browser_channel="chromium")
    result = check_browser_binary(settings)
    assert result.level == "skip"


def test_browser_binary_chromium_found(monkeypatch, tmp_path):
    # Simulate a chromium directory existing
    chromium_dir = tmp_path / "chromium-1234"
    chromium_dir.mkdir()
    monkeypatch.setattr("hotelrate.apps.mcp.cli.checks._CHROMIUM_CACHE", tmp_path)
    settings = _make_settings(playwright_browser_channel="chromium")
    result = check_browser_binary(settings)
    assert result.level == "ok"


def test_browser_binary_chromium_not_found(monkeypatch, tmp_path):
    monkeypatch.setattr("hotelrate.apps.mcp.cli.checks._CHROMIUM_CACHE", tmp_path)
    settings = _make_settings(playwright_browser_channel="chromium")
    result = check_browser_binary(settings)
    assert result.level == "error"
    assert result.fix_command == "playwright install chromium"


# ---------------------------------------------------------------------------
# check_dotenv
# ---------------------------------------------------------------------------


def test_dotenv_exists(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DEMO_MODE=true\n")
    result = check_dotenv()
    assert result.level == "ok"


def test_dotenv_missing(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    result = check_dotenv()
    assert result.level == "warning"
    assert "cp .env.example .env" in result.fix_command


# ---------------------------------------------------------------------------
# check_dotenv_settings
# ---------------------------------------------------------------------------


def test_dotenv_settings_demo():
    settings = _make_settings(demo_mode=True)
    result = check_dotenv_settings(settings)
    assert result.level == "warning"
    assert "DEMO_MODE=true" in result.message


def test_dotenv_settings_live():
    settings = _make_settings(demo_mode=False)
    result = check_dotenv_settings(settings)
    assert result.level == "ok"
    assert "DEMO_MODE=false" in result.message


# ---------------------------------------------------------------------------
# check_platform_profile
# ---------------------------------------------------------------------------


def test_platform_profile_exists(tmp_path):
    profile = tmp_path / ".playwright" / "trip-profile"
    profile.mkdir(parents=True)
    (profile / "cookies.db").write_text("data")
    settings = _make_settings(
        playwright_user_data_dir_trip=str(profile),
    )
    result = check_platform_profile("trip", settings)
    assert result.level == "ok"
    assert "1 files" in result.message


def test_platform_profile_missing(tmp_path):
    settings = _make_settings(
        playwright_user_data_dir_trip=str(tmp_path / "nonexistent"),
    )
    result = check_platform_profile("trip", settings)
    assert result.level == "warning"
    assert "not found" in result.message


def test_platform_profile_google_skip(tmp_path):
    """Google hotels is not login-required, so a missing profile is 'skip'."""
    settings = _make_settings(
        playwright_user_data_dir_google_hotels=str(tmp_path / "nonexistent"),
    )
    result = check_platform_profile("google_hotels", settings)
    assert result.level == "skip"


# ---------------------------------------------------------------------------
# _resolve_profile_dir
# ---------------------------------------------------------------------------


def test_resolve_profile_dir_per_platform(tmp_path):
    settings = _make_settings(
        playwright_user_data_dir_trip=str(tmp_path / "trip"),
    )
    result = _resolve_profile_dir("trip", settings)
    assert result == (tmp_path / "trip").resolve()


def test_resolve_profile_dir_global(tmp_path):
    settings = _make_settings(
        playwright_user_data_dir=str(tmp_path / "global"),
    )
    result = _resolve_profile_dir("trip", settings)
    assert result == (tmp_path / "global").resolve()


def test_resolve_profile_dir_default(monkeypatch, tmp_path):
    monkeypatch.setattr("hotelrate.apps.mcp.cli.checks._PROJECT_ROOT", tmp_path)
    settings = _make_settings()
    result = _resolve_profile_dir("booking", settings)
    assert "booking-profile" in str(result)


# ---------------------------------------------------------------------------
# run_all_checks
# ---------------------------------------------------------------------------


def test_run_all_checks_returns_list():
    settings = _make_settings()
    results = run_all_checks(settings)
    assert isinstance(results, list)
    assert all(isinstance(r, CheckResult) for r in results)
    # Should have at least: python, playwright, browser, dotenv, settings, + profiles
    assert len(results) >= 5

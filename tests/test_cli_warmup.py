"""Tests for ``hotelrate.apps.mcp.cli.warmup``."""

from __future__ import annotations

from pathlib import Path

import pytest

from hotelrate.apps.mcp.cli.warmup import (
    PLATFORM_LABELS,
    PLATFORM_LOGIN_REQUIRED,
    PLATFORM_LOGIN_URLS,
    resolve_profile_dir,
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
# Constants
# ---------------------------------------------------------------------------


def test_platform_login_urls_has_required_keys():
    for p in PLATFORM_LOGIN_REQUIRED:
        assert p in PLATFORM_LOGIN_URLS
        assert PLATFORM_LOGIN_URLS[p].startswith("https://")


def test_platform_labels_match_urls():
    for p in PLATFORM_LOGIN_URLS:
        assert p in PLATFORM_LABELS


def test_login_required_list():
    assert "trip" in PLATFORM_LOGIN_REQUIRED
    assert "booking" in PLATFORM_LOGIN_REQUIRED
    assert "agoda" in PLATFORM_LOGIN_REQUIRED


# ---------------------------------------------------------------------------
# resolve_profile_dir — 3-tier resolution
# ---------------------------------------------------------------------------


def test_resolve_per_platform(tmp_path):
    """Per-platform setting takes priority over global and default."""
    settings = _make_settings(
        playwright_user_data_dir_trip=str(tmp_path / "trip-custom"),
        playwright_user_data_dir=str(tmp_path / "global"),
    )
    result = resolve_profile_dir("trip", settings)
    assert result == (tmp_path / "trip-custom").resolve()


def test_resolve_global_fallback(tmp_path):
    """When no per-platform dir is set, fall back to global."""
    settings = _make_settings(
        playwright_user_data_dir=str(tmp_path / "global"),
    )
    result = resolve_profile_dir("trip", settings)
    assert result == (tmp_path / "global").resolve()


def test_resolve_default_fallback():
    """When nothing is configured, use cwd/.playwright/<platform>-profile."""
    settings = _make_settings()
    result = resolve_profile_dir("booking", settings)
    expected = (Path.cwd() / ".playwright" / "booking-profile").resolve()
    assert result == expected


def test_resolve_tilde_expansion(tmp_path, monkeypatch):
    """Tilde paths should be expanded."""
    settings = _make_settings(
        playwright_user_data_dir_agoda="~/agoda-profile-test",
    )
    result = resolve_profile_dir("agoda", settings)
    assert "~" not in str(result)
    assert result.is_absolute()


def test_resolve_unknown_platform_uses_global(tmp_path):
    """Unknown platform falls back to global, then default."""
    settings = _make_settings(
        playwright_user_data_dir=str(tmp_path / "global"),
    )
    result = resolve_profile_dir("unknown_plat", settings)
    assert result == (tmp_path / "global").resolve()


def test_resolve_all_empty():
    """All dirs empty → default path."""
    settings = _make_settings(
        playwright_user_data_dir="",
        playwright_user_data_dir_trip="",
    )
    result = resolve_profile_dir("trip", settings)
    assert "trip-profile" in str(result)


def test_resolve_strips_whitespace(tmp_path):
    """Leading/trailing whitespace should be stripped."""
    settings = _make_settings(
        playwright_user_data_dir_trip=f"  {tmp_path / 'trip'}  ",
    )
    result = resolve_profile_dir("trip", settings)
    assert result == (tmp_path / "trip").resolve()

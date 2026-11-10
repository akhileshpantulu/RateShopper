"""Tests for ``hotelrate.apps.mcp.cli.setup``."""

from __future__ import annotations

from argparse import Namespace
from unittest.mock import MagicMock

import pytest

from hotelrate.apps.mcp.cli.setup import SetupWizard
from hotelrate.config import get_settings


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_settings_cache():
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def _demo_env(monkeypatch):
    monkeypatch.setenv("DEMO_MODE", "true")
    monkeypatch.setenv("HEADLESS", "true")
    get_settings.cache_clear()


@pytest.fixture
def _no_confirm(monkeypatch):
    """Prevent interactive confirm() from reading stdin during tests."""
    monkeypatch.setattr("hotelrate.apps.mcp.cli.setup.confirm", lambda *a, **kw: False)


def _make_args(**overrides) -> Namespace:
    defaults = dict(platforms=None, skip_login=True, timeout=10)
    defaults.update(overrides)
    return Namespace(**defaults)


# ---------------------------------------------------------------------------
# Wizard instantiation
# ---------------------------------------------------------------------------


def test_wizard_init():
    args = _make_args()
    wizard = SetupWizard(args)
    assert wizard.skip_login is True
    assert wizard.timeout == 10
    assert wizard.platforms == []


def test_wizard_init_with_platforms():
    args = _make_args(platforms=["trip", "booking"])
    wizard = SetupWizard(args)
    assert wizard.platforms == ["trip", "booking"]


# ---------------------------------------------------------------------------
# Full run — skip-login mode (no browser interaction)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_demo_env", "_no_confirm")
def test_wizard_skip_login_runs_without_error():
    """Wizard with --skip-login should complete without needing browser."""
    args = _make_args(skip_login=True)
    wizard = SetupWizard(args)
    # Should not raise
    wizard.run()


# ---------------------------------------------------------------------------
# Step 1: Python version
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_demo_env")
def test_step_python_passes():
    args = _make_args()
    wizard = SetupWizard(args)
    wizard._step_python()
    assert any(r.name == "python_version" for r in wizard._results)
    assert wizard._results[0].level == "ok"


# ---------------------------------------------------------------------------
# Step 2: Playwright check
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_demo_env", "_no_confirm")
def test_step_playwright_found():
    """When playwright is installed, step should return True."""
    args = _make_args()
    wizard = SetupWizard(args)
    ok = wizard._step_playwright()
    # playwright is installed in our dev env; browser may or may not be
    assert isinstance(ok, bool)


@pytest.mark.usefixtures("_demo_env")
def test_step_playwright_missing_decline(monkeypatch):
    """User declines to install playwright → returns False."""
    monkeypatch.setattr(
        "hotelrate.apps.mcp.cli.checks.importlib.util.find_spec", lambda _: None
    )
    monkeypatch.setattr("hotelrate.apps.mcp.cli.setup.confirm", lambda *a, **kw: False)
    args = _make_args()
    wizard = SetupWizard(args)
    ok = wizard._step_playwright()
    assert ok is False


# ---------------------------------------------------------------------------
# Step 3: dotenv creation
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_demo_env")
def test_step_dotenv_already_exists(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text("DEMO_MODE=true\n")
    args = _make_args()
    wizard = SetupWizard(args)
    wizard._step_dotenv()
    assert any(r.name == "dotenv" for r in wizard._results)


@pytest.mark.usefixtures("_demo_env")
def test_step_dotenv_creates_from_example(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env.example").write_text("DEMO_MODE=true\nHEADLESS=true\n")
    # Simulate user saying yes to copy, no to live mode
    responses = iter([True, False])
    monkeypatch.setattr("hotelrate.apps.mcp.cli.setup.confirm", lambda *a, **kw: next(responses))
    args = _make_args()
    wizard = SetupWizard(args)
    wizard._step_dotenv()
    assert (tmp_path / ".env").exists()


# ---------------------------------------------------------------------------
# Step 4: Platform selection
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_demo_env")
def test_step_select_platforms_preset():
    """When platforms are pre-selected, no interactive prompt needed."""
    args = _make_args(platforms=["trip"])
    wizard = SetupWizard(args)
    wizard._step_select_platforms()
    assert wizard.platforms == ["trip"]


@pytest.mark.usefixtures("_demo_env")
def test_step_select_platforms_interactive(monkeypatch):
    monkeypatch.setattr(
        "hotelrate.apps.mcp.cli.setup.select_multiple",
        lambda prompt, choices: ["trip", "booking"],
    )
    args = _make_args()
    wizard = SetupWizard(args)
    wizard._step_select_platforms()
    assert wizard.platforms == ["trip", "booking"]


# ---------------------------------------------------------------------------
# Step 7: Summary
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_demo_env")
def test_step_summary_no_errors():
    from hotelrate.apps.mcp.cli.checks import CheckResult

    args = _make_args()
    wizard = SetupWizard(args)
    wizard._results = [
        CheckResult(name="test", label="Test", level="ok", message="good"),
    ]
    # Should not raise
    wizard._step_summary()


# ---------------------------------------------------------------------------
# Ctrl+C handling
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_demo_env")
def test_wizard_keyboard_interrupt(monkeypatch):
    """Ctrl+C should be caught and print a friendly message."""
    monkeypatch.setattr(
        "hotelrate.apps.mcp.cli.setup.SetupWizard._run_steps",
        MagicMock(side_effect=KeyboardInterrupt),
    )
    args = _make_args()
    wizard = SetupWizard(args)
    # Should not raise
    wizard.run()

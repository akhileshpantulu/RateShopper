"""Tests for ``hotelrate.apps.mcp.cli.doctor``."""

from __future__ import annotations

import json
from argparse import Namespace
from io import StringIO
from unittest.mock import patch

import pytest

from hotelrate.apps.mcp.cli.doctor import DoctorCommand
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


# ---------------------------------------------------------------------------
# Basic run
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_demo_env")
def test_doctor_runs_without_error():
    args = Namespace(probe=False, json_output=False)
    cmd = DoctorCommand(args)
    exit_code = cmd.run()
    # In demo mode with playwright installed, may have warnings but not necessarily errors
    assert exit_code in (0, 1)


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_demo_env")
def test_doctor_json_output():
    args = Namespace(probe=False, json_output=True)
    cmd = DoctorCommand(args)

    buf = StringIO()
    with patch("sys.stdout", buf):
        cmd.run()
    data = json.loads(buf.getvalue())

    assert "results" in data
    assert "summary" in data
    assert isinstance(data["results"], list)
    assert "passed" in data["summary"]
    assert "errors" in data["summary"]
    assert "warnings" in data["summary"]

    # Every result should have required fields
    for r in data["results"]:
        assert "name" in r
        assert "label" in r
        assert "level" in r
        assert "message" in r


@pytest.mark.usefixtures("_demo_env")
def test_doctor_json_exit_code_zero_when_no_errors():
    """If all checks pass or just warn, exit code should be 0."""
    args = Namespace(probe=False, json_output=True)
    cmd = DoctorCommand(args)

    buf = StringIO()
    with patch("sys.stdout", buf):
        exit_code = cmd.run()

    data = json.loads(buf.getvalue())
    if data["summary"]["errors"] == 0:
        assert exit_code == 0
    else:
        assert exit_code == 1


# ---------------------------------------------------------------------------
# Rich output (smoke test — just make sure it doesn't crash)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_demo_env")
def test_doctor_rich_output_smoke():
    """Ensure Rich rendering doesn't blow up."""
    args = Namespace(probe=False, json_output=False)
    cmd = DoctorCommand(args)
    # Should not raise
    exit_code = cmd.run()
    assert isinstance(exit_code, int)


# ---------------------------------------------------------------------------
# Probe flag (without actually running probes — we mock)
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("_demo_env")
def test_doctor_probe_flag():
    """--probe should trigger probe checks (mocked to avoid real browser)."""
    from hotelrate.apps.mcp.cli.checks import CheckResult

    mock_results = [
        CheckResult(
            name="probe_trip",
            label="Trip.com session",
            level="ok",
            message="session alive (HTTP 200)",
        ),
    ]

    args = Namespace(probe=True, json_output=True)
    cmd = DoctorCommand(args)

    buf = StringIO()
    with (
        patch("hotelrate.apps.mcp.cli.doctor.run_probe_checks", return_value=mock_results),
        patch("sys.stdout", buf),
    ):
        cmd.run()

    data = json.loads(buf.getvalue())
    probe_names = [r["name"] for r in data["results"] if r["name"].startswith("probe_")]
    assert "probe_trip" in probe_names

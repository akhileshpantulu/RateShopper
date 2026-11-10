from __future__ import annotations

import argparse
import importlib.util
from pathlib import Path

from hotelrate.config import Settings
from hotelrate.schemas import Platform


def _load_live_meta_quote_module():
    root = Path(__file__).resolve().parents[1]
    path = root / "scripts" / "live_meta_quote.py"
    spec = importlib.util.spec_from_file_location("scripts.live_meta_quote", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_build_runtime_settings_assigns_missing_platform_profiles(monkeypatch) -> None:
    module = _load_live_meta_quote_module()
    settings = Settings(
        _env_file=None,
        playwright_user_data_dir="/tmp/shared-profile",
        playwright_user_data_dir_trip="/tmp/trip-profile",
        playwright_user_data_dir_booking="",
        playwright_user_data_dir_agoda="",
        playwright_user_data_dir_google_hotels="",
        google_skip=True,
        demo_mode=False,
    )
    monkeypatch.setattr(module, "get_settings", lambda: settings)

    args = argparse.Namespace(demo_mode=False, enable_google=True)
    resolved = module.build_runtime_settings(args, [Platform.BOOKING, Platform.GOOGLE_HOTELS])

    assert resolved.google_skip is False
    assert resolved.playwright_user_data_dir_booking.endswith("/.playwright/booking-profile")
    assert resolved.playwright_user_data_dir_google_hotels == ""


def test_build_runtime_settings_skips_google_warning_in_demo_mode(monkeypatch, capsys) -> None:
    module = _load_live_meta_quote_module()
    settings = Settings(
        _env_file=None,
        google_skip=True,
        demo_mode=False,
    )
    monkeypatch.setattr(module, "get_settings", lambda: settings)

    args = argparse.Namespace(demo_mode=True, enable_google=False)
    resolved = module.build_runtime_settings(args, [Platform.GOOGLE_HOTELS])

    captured = capsys.readouterr()
    assert captured.err == ""
    assert resolved.demo_mode is True


def test_configure_playwright_driver_uses_system_node(monkeypatch) -> None:
    module = _load_live_meta_quote_module()
    monkeypatch.delenv("PLAYWRIGHT_NODEJS_PATH", raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda name: "/opt/homebrew/bin/node" if name == "node" else None)

    args = argparse.Namespace(use_system_playwright_node=True)
    module.configure_playwright_driver(args, [])

    assert module.os.environ["PLAYWRIGHT_NODEJS_PATH"] == "/opt/homebrew/bin/node"


def test_configure_playwright_driver_auto_uses_system_node_for_google(monkeypatch) -> None:
    module = _load_live_meta_quote_module()
    monkeypatch.delenv("PLAYWRIGHT_NODEJS_PATH", raising=False)
    monkeypatch.setattr(module.shutil, "which", lambda name: "/opt/homebrew/bin/node" if name == "node" else None)

    args = argparse.Namespace(use_system_playwright_node=False)
    module.configure_playwright_driver(args, [Platform.GOOGLE_HOTELS])

    assert module.os.environ["PLAYWRIGHT_NODEJS_PATH"] == "/opt/homebrew/bin/node"


def test_configure_playwright_driver_keeps_default_when_google_not_requested(monkeypatch) -> None:
    module = _load_live_meta_quote_module()
    monkeypatch.delenv("PLAYWRIGHT_NODEJS_PATH", raising=False)

    args = argparse.Namespace(use_system_playwright_node=False)
    module.configure_playwright_driver(args, [Platform.BOOKING])

    assert "PLAYWRIGHT_NODEJS_PATH" not in module.os.environ

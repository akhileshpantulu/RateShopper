"""``hotelrate-mcp doctor`` — environment health diagnostics.

Groups checks into logical sections (Prerequisites, Configuration, Platform
Profiles, and optionally Session Probes) and prints a coloured summary.
Supports ``--json`` for machine-readable output and ``--probe`` for live
session checks.
"""

from __future__ import annotations

import argparse
import asyncio
import json as json_mod
import sys

from hotelrate.apps.mcp.cli.checks import (
    CheckResult,
    PLATFORMS_LOGIN_REQUIRED,
    check_browser_binary,
    check_dotenv,
    check_dotenv_settings,
    check_platform_profile,
    check_playwright_package,
    check_python_version,
    run_probe_checks,
)
from hotelrate.apps.mcp.cli.ui import (
    console,
    print_banner,
    print_check,
    print_section,
    print_summary,
)
from hotelrate.config import get_settings


class DoctorCommand:
    """Orchestrates all diagnostic checks and renders output."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.probe = getattr(args, "probe", False)
        self.json_output = getattr(args, "json_output", False)

    # ── public interface ──────────────────────────────────────────────

    def run(self) -> int:
        """Execute checks, print results, return exit code (0 ok, 1 errors)."""
        settings = get_settings()
        all_results: list[CheckResult] = []

        # 1) Prerequisites
        prereqs = [
            check_python_version(),
            check_playwright_package(),
            check_browser_binary(settings),
        ]
        all_results.extend(prereqs)

        # 2) Configuration
        config_results = [
            check_dotenv(),
            check_dotenv_settings(settings),
        ]
        all_results.extend(config_results)

        # 3) Platform profiles
        profile_results = [
            check_platform_profile(p, settings) for p in PLATFORMS_LOGIN_REQUIRED
        ]
        all_results.extend(profile_results)

        # 4) Optional: live session probes
        probe_results: list[CheckResult] = []
        if self.probe:
            probe_results = asyncio.run(run_probe_checks(settings))
            all_results.extend(probe_results)

        # ── output ────────────────────────────────────────────────────
        if self.json_output:
            return self._output_json(all_results)
        return self._output_rich(prereqs, config_results, profile_results, probe_results)

    # ── output modes ──────────────────────────────────────────────────

    def _output_rich(
        self,
        prereqs: list[CheckResult],
        config: list[CheckResult],
        profiles: list[CheckResult],
        probes: list[CheckResult],
    ) -> int:
        print_banner("hotelrate-mcp doctor")

        print_section("Prerequisites")
        for r in prereqs:
            print_check(r.label, r.level, r.message, r.fix_command)

        console.print()
        print_section("Configuration")
        for r in config:
            print_check(r.label, r.level, r.message, r.fix_command)

        console.print()
        print_section("Platform Profiles")
        for r in profiles:
            print_check(r.label, r.level, r.message, r.fix_command)

        if probes:
            console.print()
            print_section("Session Probes")
            for r in probes:
                print_check(r.label, r.level, r.message, r.fix_command)

        # Aggregate stats
        all_results = prereqs + config + profiles + probes
        passed = sum(1 for r in all_results if r.level == "ok")
        errors = sum(1 for r in all_results if r.level == "error")
        warnings = sum(1 for r in all_results if r.level == "warning")
        print_summary(passed, errors, warnings)

        if errors:
            console.print("  [fix]Run [bold]hotelrate-mcp setup[/bold] to fix issues.[/fix]")
            console.print()

        return 1 if errors else 0

    def _output_json(self, results: list[CheckResult]) -> int:
        data = {
            "results": [
                {
                    "name": r.name,
                    "label": r.label,
                    "level": r.level,
                    "message": r.message,
                    "fix_command": r.fix_command,
                    "metadata": r.metadata,
                }
                for r in results
            ],
            "summary": {
                "passed": sum(1 for r in results if r.level == "ok"),
                "errors": sum(1 for r in results if r.level == "error"),
                "warnings": sum(1 for r in results if r.level == "warning"),
            },
        }
        # JSON goes to stdout (not stderr) so it can be piped.
        sys.stdout.write(json_mod.dumps(data, indent=2) + "\n")
        return 1 if data["summary"]["errors"] else 0

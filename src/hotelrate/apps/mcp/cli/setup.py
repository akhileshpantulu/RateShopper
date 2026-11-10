"""``hotelrate-mcp setup`` — interactive first-time setup wizard.

Walks the user through seven steps:

1. Check Python version
2. Check / offer to install Playwright + browser
3. Create .env from .env.example
4. Select platforms
5. Browser login warmup per platform
6. Verify sessions via lightweight probe
7. Summary table
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import subprocess
import sys
from pathlib import Path

from hotelrate.apps.mcp.cli.checks import (
    CheckResult,
    PLATFORMS_LOGIN_REQUIRED,
    check_browser_binary,
    check_dotenv,
    check_playwright_package,
    check_python_version,
    probe_platform_session,
)
from hotelrate.apps.mcp.cli.ui import (
    console,
    confirm,
    print_banner,
    print_check,
    print_section,
    print_summary,
    select_multiple,
)
from hotelrate.config import get_settings


class SetupWizard:
    """Seven-step interactive setup wizard."""

    def __init__(self, args: argparse.Namespace) -> None:
        self.platforms: list[str] = getattr(args, "platforms", None) or []
        self.skip_login: bool = getattr(args, "skip_login", False)
        self.timeout: int = getattr(args, "timeout", 300)
        self._results: list[CheckResult] = []

    # ── public ────────────────────────────────────────────────────────

    def run(self) -> None:
        """Run the full setup wizard, handling Ctrl+C gracefully."""
        try:
            self._run_steps()
        except KeyboardInterrupt:
            console.print()
            console.print("  [warn]Setup interrupted. Resume anytime with:[/warn]")
            console.print("    [fix]hotelrate-mcp setup[/fix]")
            console.print()

    # ── internal ──────────────────────────────────────────────────────

    def _run_steps(self) -> None:
        print_banner("hotelrate-mcp setup", "Interactive first-time configuration")

        # Step 1: Python version
        self._step_python()

        # Step 2: Playwright + browser
        pw_ok = self._step_playwright()

        # Step 3: .env file
        self._step_dotenv()

        # Step 4: Select platforms (skip in demo mode or if pre-selected)
        settings = get_settings()
        if settings.demo_mode and not self.platforms:
            console.print("  [skip]Step 4/7 — Platform selection skipped (DEMO_MODE=true)[/skip]")
            console.print()
        else:
            self._step_select_platforms()

        # Step 5: Browser login warmup
        if self.skip_login or not pw_ok or not self.platforms:
            reason = (
                "skipped (--skip-login)"
                if self.skip_login
                else "skipped (playwright not available)"
                if not pw_ok
                else "skipped (no platforms selected)"
            )
            console.print(f"  [skip]Step 5/7 — Browser login {reason}[/skip]")
            console.print()
        else:
            self._step_login_warmup()

        # Step 6: Session probes
        if self.skip_login or not pw_ok or not self.platforms:
            console.print("  [skip]Step 6/7 — Session verification skipped[/skip]")
            console.print()
        else:
            self._step_verify_sessions()

        # Step 7: Summary
        self._step_summary()

    # ── steps ─────────────────────────────────────────────────────────

    def _step_python(self) -> None:
        console.print("  [bold]Step 1/7[/bold] — Python version")
        result = check_python_version()
        self._results.append(result)
        print_check(result.label, result.level, result.message)
        console.print()
        if result.level == "error":
            console.print("  [err]Fatal: Python 3.12+ is required. Aborting.[/err]")
            raise SystemExit(1)

    def _step_playwright(self) -> bool:
        """Check and optionally install Playwright. Returns True if available."""
        console.print("  [bold]Step 2/7[/bold] — Playwright & browser")

        # Check package
        pkg = check_playwright_package()
        self._results.append(pkg)
        print_check(pkg.label, pkg.level, pkg.message, pkg.fix_command)

        if pkg.level == "error":
            if confirm("Install playwright now? (pip install playwright)"):
                self._run_pip_install("playwright")
                # Recheck
                pkg = check_playwright_package()
                self._results[-1] = pkg
                print_check(pkg.label, pkg.level, pkg.message)
            else:
                console.print("  [warn]Playwright not installed — login steps will be skipped.[/warn]")
                console.print()
                return False

        if pkg.level != "ok":
            console.print()
            return False

        # Check browser binary
        settings = get_settings()
        browser = check_browser_binary(settings)
        self._results.append(browser)
        print_check(browser.label, browser.level, browser.message, browser.fix_command)

        if browser.level == "error":
            if confirm("Install chromium browser now? (playwright install chromium)"):
                self._run_subprocess(["playwright", "install", "chromium"])
                browser = check_browser_binary(settings)
                self._results[-1] = browser
                print_check(browser.label, browser.level, browser.message)

        console.print()
        return browser.level in ("ok", "skip")

    def _step_dotenv(self) -> None:
        console.print("  [bold]Step 3/7[/bold] — Environment file (.env)")
        result = check_dotenv()
        self._results.append(result)
        print_check(result.label, result.level, result.message, result.fix_command)

        if result.level in ("warning", "error"):
            example = Path(".env.example")
            target = Path(".env")
            if example.exists() and not target.exists():
                if confirm("Create .env from .env.example?"):
                    shutil.copy2(example, target)
                    console.print("    [ok]Created .env[/ok]")

                    # Ask about demo mode
                    if confirm("Enable live mode? (set DEMO_MODE=false)", default=False):
                        content = target.read_text()
                        content = content.replace("DEMO_MODE=true", "DEMO_MODE=false")
                        target.write_text(content)
                        console.print("    [ok]Set DEMO_MODE=false[/ok]")

                    # Reload settings after .env creation
                    get_settings.cache_clear()

                    result = check_dotenv()
                    self._results[-1] = result
                    print_check(result.label, result.level, result.message)

        console.print()

    def _step_select_platforms(self) -> None:
        console.print("  [bold]Step 4/7[/bold] — Select platforms")
        if not self.platforms:
            self.platforms = select_multiple(
                "Which platforms do you want to configure?",
                PLATFORMS_LOGIN_REQUIRED,
            )
        if self.platforms:
            console.print(f"    Selected: {', '.join(self.platforms)}")
        else:
            console.print("    [skip]No platforms selected[/skip]")
        console.print()

    def _step_login_warmup(self) -> None:
        console.print("  [bold]Step 5/7[/bold] — Browser login")
        console.print("  A browser window will open for each platform.")
        console.print("  Log in manually, then close the browser to continue.")
        console.print()

        for platform in self.platforms:
            try:
                self._warmup_single(platform)
            except KeyboardInterrupt:
                console.print(f"\n  [warn]Skipped {platform}[/warn]")
                if not confirm("Continue with remaining platforms?"):
                    break

    def _warmup_single(self, platform: str) -> None:
        from hotelrate.apps.mcp.cli.warmup import PLATFORM_LABELS, open_login_browser

        label = PLATFORM_LABELS.get(platform, platform)
        console.print(f"    Opening {label}...")

        settings = get_settings()
        status_messages: list[str] = []

        def on_status(msg: str) -> None:
            status_messages.append(msg)
            console.print(f"      [dim]{msg}[/dim]")

        success = asyncio.run(
            open_login_browser(
                platform,
                settings,
                timeout_seconds=self.timeout,
                on_status=on_status,
            )
        )

        if success:
            console.print(f"    [ok]{PLATFORM_LABELS.get(platform, platform)} — done[/ok]")
        else:
            console.print(f"    [warn]{PLATFORM_LABELS.get(platform, platform)} — timed out or failed[/warn]")
        console.print()

    def _step_verify_sessions(self) -> None:
        console.print("  [bold]Step 6/7[/bold] — Verifying sessions")

        settings = get_settings()
        for platform in self.platforms:
            result = asyncio.run(probe_platform_session(platform, settings))
            self._results.append(result)
            print_check(result.label, result.level, result.message, result.fix_command)
        console.print()

    def _step_summary(self) -> None:
        console.print("  [bold]Step 7/7[/bold] — Summary")
        print_section("Results")
        for r in self._results:
            print_check(r.label, r.level, r.message)

        passed = sum(1 for r in self._results if r.level == "ok")
        errors = sum(1 for r in self._results if r.level == "error")
        warnings = sum(1 for r in self._results if r.level == "warning")
        print_summary(passed, errors, warnings)

        if errors == 0:
            console.print("  [ok]Setup complete! You can now use hotelrate-mcp.[/ok]")
        else:
            console.print("  [warn]Some issues remain. Run [bold]hotelrate-mcp doctor[/bold] for details.[/warn]")
        console.print()

    # ── helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _run_pip_install(package: str) -> None:
        console.print(f"    Running: pip install {package}")
        subprocess.run(  # noqa: S603
            [sys.executable, "-m", "pip", "install", package],
            check=False,
        )

    @staticmethod
    def _run_subprocess(cmd: list[str]) -> None:
        console.print(f"    Running: {' '.join(cmd)}")
        subprocess.run(cmd, check=False)  # noqa: S603

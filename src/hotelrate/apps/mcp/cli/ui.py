"""Shared Rich console helpers for CLI commands.

Provides a consistent look-and-feel for banners, check results, confirm
prompts, and multi-select menus.  Uses Rich (already a core dependency) and
plain ``input()`` — no additional packages needed.
"""

from __future__ import annotations

import sys

from rich.console import Console
from rich.theme import Theme

CLI_THEME = Theme(
    {
        "banner": "bold cyan",
        "ok": "bold green",
        "warn": "bold yellow",
        "err": "bold red",
        "skip": "dim",
        "fix": "dim cyan",
        "heading": "bold underline",
    }
)

# stderr so Rich output never pollutes MCP's stdout protocol channel.
console = Console(theme=CLI_THEME, stderr=True)

# Markers — fall back to ASCII if the terminal doesn't support Unicode.
_SUPPORTS_UNICODE = sys.stdout.encoding and sys.stdout.encoding.lower().startswith("utf")
MARK_OK = "✓" if _SUPPORTS_UNICODE else "[OK]"
MARK_WARN = "!" if _SUPPORTS_UNICODE else "[!!]"
MARK_ERR = "✗" if _SUPPORTS_UNICODE else "[FAIL]"
MARK_SKIP = "•" if _SUPPORTS_UNICODE else "[-]"


def print_banner(title: str, subtitle: str = "") -> None:
    """Print a prominent section header."""
    console.print()
    console.print(f"  [banner]{title}[/banner]")
    if subtitle:
        console.print(f"  {subtitle}")
    console.print()


def print_section(title: str) -> None:
    """Print a group heading inside a command's output."""
    console.print(f"  [heading]{title}[/heading]")


def print_check(label: str, level: str, message: str, fix_command: str = "") -> None:
    """Print a single diagnostic check line with a coloured marker.

    *level* must be one of ``"ok"``, ``"warning"``, ``"error"``, ``"skip"``.
    """
    if level == "ok":
        marker = f"[ok]{MARK_OK}[/ok]"
    elif level == "warning":
        marker = f"[warn]{MARK_WARN}[/warn]"
    elif level == "error":
        marker = f"[err]{MARK_ERR}[/err]"
    else:
        marker = f"[skip]{MARK_SKIP}[/skip]"

    console.print(f"    {marker} {label} — {message}")
    if fix_command and level in ("error", "warning"):
        console.print(f"      [fix]fix: {fix_command}[/fix]")


def print_summary(passed: int, errors: int, warnings: int) -> None:
    """Print an aggregate result line."""
    parts: list[str] = []
    parts.append(f"[ok]{passed} passed[/ok]")
    parts.append(f"[err]{errors} error(s)[/err]" if errors else f"{errors} error(s)")
    parts.append(f"[warn]{warnings} warning(s)[/warn]" if warnings else f"{warnings} warning(s)")
    console.print()
    console.print("  " + "  •  ".join(parts))
    console.print()


# ── Interactive prompts (plain input(), no extra deps) ────────────────


def confirm(prompt: str, *, default: bool = True) -> bool:
    """Ask a yes/no question, return ``True`` for yes."""
    hint = "Y/n" if default else "y/N"
    try:
        answer = input(f"  {prompt} [{hint}] ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return default
    if not answer:
        return default
    return answer.startswith("y")


def select_multiple(prompt: str, choices: list[str]) -> list[str]:
    """Show a numbered list; user types space-separated indices to toggle.

    Returns the selected labels.
    """
    console.print(f"  {prompt}")
    for idx, label in enumerate(choices, 1):
        console.print(f"    [{idx}] {label}")
    console.print()
    try:
        raw = input("  Enter numbers (space-separated), or 'all': ").strip()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return []
    if raw.lower() == "all":
        return list(choices)
    selected: list[str] = []
    for token in raw.split():
        try:
            i = int(token) - 1
            if 0 <= i < len(choices):
                selected.append(choices[i])
        except ValueError:
            continue
    return selected

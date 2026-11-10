"""
Rich TUI for live meta quote crawl — geek-style cards and live progress.
Updates are throttled to reduce terminal flicker.
"""
from __future__ import annotations

import time
from typing import Any

from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.panel import Panel
from rich.table import Table
from rich.text import Text
from rich.theme import Theme

# Throttle: min seconds between Live updates to avoid flicker
LIVE_UPDATE_INTERVAL = 0.45

# Geek terminal theme: amber/green on dark, monospace vibes
CRAWL_THEME = Theme(
    {
        "crawl.header": "bold cyan",
        "crawl.subtitle": "dim cyan",
        "crawl.platform": "bold",
        "crawl.pending": "dim white",
        "crawl.running": "bold yellow",
        "crawl.ok": "bold green",
        "crawl.fail": "bold red",
        "crawl.meta": "dim cyan",
        "crawl.url": "dim blue",
        "crawl.highlight": "bold magenta",
    }
)


def _truncate(s: str, max_len: int = 52) -> str:
    s = (s or "").strip()
    if len(s) <= max_len:
        return s
    return s[: max_len - 3] + "…"


def _format_elapsed(sec: float) -> str:
    if sec < 60:
        return f"{sec:.1f}s"
    m, s = divmod(int(sec), 60)
    return f"{m}m {s}s"


def render_crawl_cards(
    request_summary: dict[str, Any],
    state: dict[str, dict[str, Any]],
    platform_order: list[str],
) -> RenderableType:
    """Build the main dashboard: header + one card per platform."""
    # Header — terminal / pipeline vibe
    header = Text()
    header.append("▐ ", style="bold cyan")
    header.append("HOTELRATE", style="crawl.header")
    header.append(" ▐ ", style="bold cyan")
    header.append("LIVE CRAWL", style="crawl.subtitle")
    header.append(" ▐", style="bold cyan")
    if request_summary:
        hotel = request_summary.get("hotel_name", "?")
        checkin = request_summary.get("checkin", "?")
        checkout = request_summary.get("checkout", "?")
        header.append(f"\n  › ", style="crawl.meta")
        header.append(_truncate(hotel, 48), style="crawl.highlight")
        header.append(f"  │  {checkin} → {checkout}", style="crawl.meta")
        header.append(f"  │  ", style="crawl.meta")
        header.append(request_summary.get("currency", "USD"), style="crawl.platform")

    header_panel = Panel(
        header,
        title="[bold] pipeline [/]",
        subtitle="[dim] playwright · multi-source · rate aggregation [/]",
        border_style="cyan",
        padding=(0, 1),
    )

    # Platform cards
    cards: list[RenderableType] = []
    for platform in platform_order:
        data = state.get(platform, {})
        phase = data.get("phase", "idle")
        last = data.get("last", {})

        # Title row
        plat_display = platform.upper().replace("_", " ")
        if phase == "start" or phase == "attempt":
            status_text = Text("◉ CRAWLING", style="crawl.running")
            status_text.append("  ▌", style="crawl.running")
        elif phase == "attempt_done" or phase == "done":
            if last.get("error") or (phase == "done" and not last.get("success", True)):
                status_text = Text("✗ FAIL", style="crawl.fail")
            else:
                status_text = Text("✓ DONE", style="crawl.ok")
        else:
            status_text = Text("○ PENDING", style="crawl.pending")

        title = Text()
        title.append(f" {plat_display} ", style="crawl.platform")
        title.append(" ", style="crawl.platform")
        title.append(status_text)

        # Content table
        table = Table(show_header=False, box=None, padding=(0, 0))
        table.add_column(style="crawl.meta", width=12)
        table.add_column(style="crawl.url")

        if phase in ("attempt", "attempt_done", "done") and last:
            if last.get("url"):
                table.add_row("URL", _truncate(last["url"], 56))
            if last.get("checkin") and last.get("checkout"):
                table.add_row("Dates", f"{last['checkin']} → {last['checkout']}")
            if "attempt_index" in last:
                table.add_row("Attempt", f"#{last['attempt_index'] + 1}")
            if "offer_count" in last:
                table.add_row("Offers", str(last["offer_count"]))
            if "structured_count" in last:
                table.add_row("Structured", str(last["structured_count"]))
            if last.get("diagnosis"):
                table.add_row("Diagnosis", _truncate(last["diagnosis"], 50))
            if "elapsed" in last:
                table.add_row("Elapsed", _format_elapsed(last["elapsed"]))
            if last.get("error"):
                table.add_row("Error", _truncate(last["error"], 50))

        if not table.rows:
            table.add_row("—", "waiting for crawl…")

        panel = Panel(
            table,
            title=title,
            border_style="green" if phase == "done" and last.get("success") else "red" if last.get("error") else "blue",
            padding=(0, 1),
        )
        cards.append(panel)

    return Group(header_panel, "", *cards)


class CrawlDashboard:
    """Live dashboard state and progress callback for Rich TUI."""

    def __init__(self, platform_order: list[str], request_summary: dict[str, Any] | None = None):
        self.console = Console(theme=CRAWL_THEME)
        self.platform_order = platform_order
        self.request_summary = request_summary or {}
        self.state: dict[str, dict[str, Any]] = {p: {"phase": "idle"} for p in platform_order}
        self._live: Live | None = None
        self._last_update_time: float = 0.0

    def _render(self) -> RenderableType:
        return render_crawl_cards(self.request_summary, self.state, self.platform_order)

    def _should_update(self, phase: str) -> bool:
        """Throttle updates to reduce flicker; always update on terminal phases."""
        if phase in ("done", "attempt_done"):
            return True
        now = time.monotonic()
        if now - self._last_update_time >= LIVE_UPDATE_INTERVAL:
            self._last_update_time = now
            return True
        return False

    async def on_progress(self, platform: str, phase: str, data: dict[str, Any]) -> None:
        if platform not in self.state:
            self.state[platform] = {"phase": "idle"}
        self.state[platform]["phase"] = phase
        self.state[platform]["last"] = data
        if self._live and self._should_update(phase):
            self._live.update(self._render())

    def print_final(self, response_dict: dict[str, Any] | None = None) -> None:
        """Optionally print a final summary (e.g. saved path)."""
        if response_dict and response_dict.get("results"):
            table = Table(title="[bold cyan]▸ SUMMARY[/]", border_style="cyan")
            table.add_column("Platform", style="crawl.platform")
            table.add_column("Offers", justify="right")
            table.add_column("Structured", justify="right")
            table.add_column("Diagnosis", style="crawl.meta")
            for r in response_dict["results"]:
                table.add_row(
                    r.get("platform", "?"),
                    str(r.get("offer_count", 0)),
                    str(r.get("structured_offer_count", 0)),
                    _truncate(r.get("diagnosis", ""), 40),
                )
            self.console.print()
            self.console.print(table)

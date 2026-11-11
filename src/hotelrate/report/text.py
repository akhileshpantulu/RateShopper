"""Plain-text rendering of a report for the terminal."""

from __future__ import annotations

from datetime import date
from typing import Any

from hotelrate.report.engine import Report, Table

MAX_CELL = 48


def render_text(report: Report, *, max_rows: int | None = 40) -> str:
    parts = [report.title, "=" * len(report.title), ""]
    for table in report.tables:
        parts.append(render_table(table, max_rows=max_rows))
        parts.append("")
    return "\n".join(parts)


def render_table(table: Table, *, max_rows: int | None = 40) -> str:
    header = [column.name for column in table.columns]
    rows = table.rows if max_rows is None else table.rows[:max_rows]
    body = [[_cell(value, column.kind) for value, column in zip(row, table.columns, strict=False)] for row in rows]
    if table.total is not None:
        body.append([_cell(value, column.kind) for value, column in zip(table.total, table.columns, strict=False)])
    widths = [max(len(cell) for cell in column) for column in zip(header, *body, strict=False)]
    lines = [table.title, "-" * len(table.title)]
    if not table.rows:
        lines.append("(no data)")
        return "\n".join(lines)
    lines.append("  ".join(name.ljust(width) for name, width in zip(header, widths, strict=False)))
    for index, row in enumerate(body):
        if table.total is not None and index == len(body) - 1:
            lines.append("  ".join("-" * width for width in widths))
        lines.append("  ".join(_align(cell, width, kind) for cell, width, kind in
                               zip(row, widths, [column.kind for column in table.columns], strict=False)))
    hidden = len(table.rows) - len(rows)
    if hidden > 0:
        lines.append(f"... {hidden} more rows in the Excel export")
    return "\n".join(lines)


def _cell(value: Any, kind: str) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value if len(value) <= MAX_CELL else value[: MAX_CELL - 3] + "..."
    if kind == "money":
        return f"{value:,.2f}"
    if kind == "pct":
        return f"{value:+.1%}"
    if kind == "share":
        return f"{value:.1%}"
    if kind == "int":
        return f"{value:,}"
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


def _align(cell: str, width: int, kind: str) -> str:
    return cell.rjust(width) if kind in {"money", "pct", "share", "int"} else cell.ljust(width)

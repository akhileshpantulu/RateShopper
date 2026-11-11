"""One-sheet Excel export of a report, in the house format.

Arial 14 throughout, header rows #16365C with white text centred across the table, subheader rows #D9D9D9, totals
#DDEBF7 across every column, no borders, no wrapping, no merged cells, no frozen panes, zoom 55%. Column A is 8 wide,
B and C are 1 wide, every table starts in column D (first at D3), and the two columns after the widest table are 1
wide. Widths are written through `xl_width` so Excel displays exactly the intended width.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.worksheet import Worksheet

from hotelrate.report.engine import Column, Report, Table

FONT_NAME = "Arial"
FONT_SIZE = 14
HEADER_FILL = "16365C"
SUBHEADER_FILL = "D9D9D9"
TOTAL_FILL = "DDEBF7"
ZOOM = 55
FIRST_COLUMN = 4  # D
FIRST_ROW = 3
GAP_ROWS = 1
MDW = 12  # max digit width in pixels for Arial 14, calibrated against Excel's Column Width dialog
NUMBER_FORMATS = {"money": "#,##0.00", "pct": "+0.0%;-0.0%;0.0%", "share": "0.0%", "int": "#,##0",
                  "date": "yyyy-mm-dd"}
# Arial letters run wider than its digits, which set Excel's width unit.
TEXT_WIDTH_FACTOR = 1.15
WIDTH_PADDING = 2


def xl_width(chars: float, mdw: int = MDW) -> float:
    """Stored width that makes Excel display exactly `chars` in the Column Width dialog."""
    return math.trunc((chars * mdw + 5) / mdw * 256) / 256


def write_report(report: Report, path: Path, *, sheet_title: str = "Rate Shop") -> Path:
    workbook = Workbook()
    _set_default_font(workbook)
    sheet = workbook.active
    sheet.title = sheet_title[:31]
    widths: dict[int, int] = {}
    row = FIRST_ROW
    last_column = FIRST_COLUMN
    for table in report.tables:
        row = _write_table(sheet, table, row, widths) + GAP_ROWS + 1
        last_column = max(last_column, FIRST_COLUMN + len(table.columns) - 1)
    for index, chars in widths.items():
        sheet.column_dimensions[get_column_letter(index)].width = xl_width(chars)
    _apply_layout(sheet, last_column)
    path.parent.mkdir(parents=True, exist_ok=True)
    workbook.save(path)
    return path


def _set_default_font(workbook: Workbook) -> None:
    # The Normal style's font sets Excel's width unit, so it must match the cells.
    for style in workbook._named_styles:  # noqa: SLF001 - openpyxl exposes no public setter
        if style.name == "Normal":
            style.font = Font(name=FONT_NAME, size=FONT_SIZE)


def _write_table(sheet: Worksheet, table: Table, row: int, widths: dict[int, int]) -> int:
    width = len(table.columns)
    columns = range(FIRST_COLUMN, FIRST_COLUMN + width)

    for column in columns:
        cell = sheet.cell(row=row, column=column, value=table.title if column == FIRST_COLUMN else None)
        cell.font = Font(name=FONT_NAME, size=FONT_SIZE, bold=True, color="FFFFFF")
        cell.fill = PatternFill("solid", fgColor=HEADER_FILL)
        cell.alignment = Alignment(horizontal="centerContinuous", vertical="center", wrap_text=False)
    row += 1

    for offset, column in enumerate(table.columns):
        cell = sheet.cell(row=row, column=FIRST_COLUMN + offset, value=column.name)
        cell.font = Font(name=FONT_NAME, size=FONT_SIZE, bold=True, color="000000")
        cell.fill = PatternFill("solid", fgColor=SUBHEADER_FILL)
        cell.alignment = Alignment(horizontal="left" if offset == 0 else "center", wrap_text=False)
        _track(widths, FIRST_COLUMN + offset, len(column.name))
    row += 1

    for values in table.rows:
        _write_row(sheet, row, table.columns, values, widths, total=False)
        row += 1
    if table.total is not None:
        _write_row(sheet, row, table.columns, table.total, widths, total=True)
        row += 1
    return row - 1


def _write_row(
    sheet: Worksheet, row: int, columns: list[Column], values: list[Any], widths: dict[int, int], *, total: bool
) -> None:
    for offset, column in enumerate(columns):
        value = values[offset] if offset < len(values) else None
        cell = sheet.cell(row=row, column=FIRST_COLUMN + offset, value=value)
        cell.font = Font(name=FONT_NAME, size=FONT_SIZE, bold=total, color="000000")
        if total:
            cell.fill = PatternFill("solid", fgColor=TOTAL_FILL)
        kind = column.kind if not isinstance(value, str) else "text"
        if kind in NUMBER_FORMATS and value is not None:
            cell.number_format = NUMBER_FORMATS[kind]
        horizontal = "left" if offset == 0 else ("right" if kind in NUMBER_FORMATS else "left")
        cell.alignment = Alignment(horizontal=horizontal, wrap_text=False)
        _track(widths, FIRST_COLUMN + offset, len(_display(value, kind)))


def _display(value: Any, kind: str) -> str:
    if value is None:
        return ""
    if kind == "money":
        return f"{value:,.2f}"
    if kind == "pct":
        return f"{value:+.1%}"
    if kind == "share":
        return f"{value:.1%}"
    if kind == "int":
        return f"{value:,}"
    if isinstance(value, (date, datetime)):
        return value.strftime("%Y-%m-%d")
    return str(value)


def _track(widths: dict[int, int], column: int, length: int) -> None:
    chars = math.ceil(length * TEXT_WIDTH_FACTOR) + WIDTH_PADDING
    widths[column] = max(widths.get(column, 0), chars)


def _apply_layout(sheet: Worksheet, last_table_column: int) -> None:
    sheet.column_dimensions["A"].width = xl_width(8)
    sheet.column_dimensions["B"].width = xl_width(1)
    sheet.column_dimensions["C"].width = xl_width(1)
    for offset in (1, 2):
        sheet.column_dimensions[get_column_letter(last_table_column + offset)].width = xl_width(1)
    sheet.sheet_view.zoomScale = ZOOM
    sheet.freeze_panes = None
    # Printing: landscape, one page wide.
    sheet.page_setup.orientation = "landscape"
    sheet.sheet_properties.pageSetUpPr.fitToPage = True
    sheet.page_setup.fitToWidth = 1
    sheet.page_setup.fitToHeight = 0


def check_layout(path: Path) -> list[str]:
    """Problems with the house layout in a saved workbook; empty when it complies."""
    problems: list[str] = []
    workbook = load_workbook(path)
    for sheet in workbook.worksheets:
        expected = {"A": xl_width(8), "B": xl_width(1), "C": xl_width(1)}
        for letter, width in expected.items():
            if sheet.column_dimensions[letter].width != width:
                problems.append(f"{sheet.title}: column {letter} width {sheet.column_dimensions[letter].width}")
        if sheet["D3"].value is None:
            problems.append(f"{sheet.title}: first table does not start at D3")
        last = sheet.max_column
        for offset in (1, 2):
            letter = get_column_letter(last + offset)
            if sheet.column_dimensions[letter].width != xl_width(1):
                problems.append(f"{sheet.title}: trailing spacer {letter} width {sheet.column_dimensions[letter].width}")
        if sheet.sheet_view.zoomScale != ZOOM:
            problems.append(f"{sheet.title}: zoom {sheet.sheet_view.zoomScale}")
        if sheet.freeze_panes is not None:
            problems.append(f"{sheet.title}: frozen panes at {sheet.freeze_panes}")
        if sheet.merged_cells.ranges:
            problems.append(f"{sheet.title}: merged cells {list(sheet.merged_cells.ranges)}")
        for row in sheet.iter_rows():
            for cell in row:
                if cell.value is None and cell.fill.fill_type is None:
                    continue
                border = cell.border
                if any(getattr(border, side).style for side in ("left", "right", "top", "bottom")):
                    problems.append(f"{sheet.title}: border at {cell.coordinate}")
                if cell.alignment.wrap_text:
                    problems.append(f"{sheet.title}: wrapped text at {cell.coordinate}")
                if cell.font.name != FONT_NAME or cell.font.sz != FONT_SIZE:
                    problems.append(f"{sheet.title}: font {cell.font.name} {cell.font.sz} at {cell.coordinate}")
    return problems

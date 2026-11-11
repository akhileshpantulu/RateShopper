"""Competitive pricing report built from stored observations (docs/DESIGN.md, section 7)."""

from hotelrate.report.data import CalendarSnapshot, EventInfo, HotelInfo, OfferPoint, ReportData, load_report_data
from hotelrate.report.engine import Column, Report, Table, build_report
from hotelrate.report.excel import check_layout, write_report
from hotelrate.report.text import render_text

__all__ = [
    "CalendarSnapshot",
    "Column",
    "EventInfo",
    "HotelInfo",
    "OfferPoint",
    "Report",
    "ReportData",
    "Table",
    "build_report",
    "check_layout",
    "load_report_data",
    "render_text",
    "write_report",
]

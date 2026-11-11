"""Report sections on small hand-checked data, and the Excel export's house layout."""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from openpyxl import load_workbook

from hotelrate.report import (
    CalendarSnapshot,
    EventInfo,
    HotelInfo,
    OfferPoint,
    ReportData,
    build_report,
    check_layout,
    write_report,
)
from hotelrate.report.engine import (
    changes_by_month,
    forward_averages,
    forward_points,
    forward_vs_compset,
    changes_by_window,
    event_response,
    parity,
    rate_position,
    room_ladder,
    sellouts,
)
from hotelrate.report.excel import HEADER_FILL, SUBHEADER_FILL, TOTAL_FILL, xl_width

TARGET, COMP_A, COMP_B = HotelInfo(1, "Target", "target"), HotelInfo(2, "Comp A", "comp"), HotelInfo(3, "Comp B", "comp")
LATEST, PREVIOUS = date(2027, 1, 1), date(2026, 12, 25)
D = date


def _calendar(hotel: HotelInfo, rates: dict[date, float], no_price=(), fetch_date: date = LATEST) -> CalendarSnapshot:
    return CalendarSnapshot(hotel.id, 1, fetch_date, dict(rates), set(no_price))


def _data(**overrides) -> ReportData:
    values = {
        "study_name": "Test", "city": "London", "currency": "GBP", "adults": 2, "calendar_nights": [1],
        "offers_nights": 1, "hotels": [TARGET, COMP_A, COMP_B],
    }
    values.update(overrides)
    return ReportData(**values)


def _offer(check_in: date, channel: str, price: float, *, official=False, room=None, fc=None, breakfast=None):
    return OfferPoint(TARGET.id, check_in, channel, official, False, room, fc, breakfast, price)


# -- rate position -----------------------------------------------------------------------------------------------------


def _position_data() -> ReportData:
    # 2027-01-04 and 2027-02-01 are Mondays, 2027-01-05 a Tuesday.
    return _data(calendars={
        (1, 1): _calendar(TARGET, {D(2027, 1, 4): 200, D(2027, 1, 5): 220, D(2027, 2, 1): 300}),
        (2, 1): _calendar(COMP_A, {D(2027, 1, 4): 100, D(2027, 1, 5): 200, D(2027, 2, 1): 250}),
        (3, 1): _calendar(COMP_B, {D(2027, 1, 4): 300, D(2027, 1, 5): 240}),
    })


def test_rate_position_by_month() -> None:
    table = rate_position(_position_data(), 1, by="month")
    assert table.rows == [
        ["Jan 2027", 210, 210, 100, 300, 0.0, 2],
        ["Feb 2027", 300, 250, 250, 250, 0.2, 1],
    ]
    assert table.total == ["All dates", 240, 223.33, 100, 300, 0.0746, 3]


def test_rate_position_by_weekday() -> None:
    table = rate_position(_position_data(), 1, by="weekday")
    assert [row[0] for row in table.rows] == ["Monday", "Tuesday"]
    assert table.rows[0] == ["Monday", 250, 225, 100, 300, 0.1111, 2]


def test_rate_position_without_target_is_empty() -> None:
    table = rate_position(_data(hotels=[COMP_A, COMP_B]), 1, by="month")
    assert table.rows == [] and table.total is None


# -- forward averages -------------------------------------------------------------------------------------------------


def test_forward_averages_by_hotel_and_comp_set_median() -> None:
    table = forward_averages(_position_data(), 1)
    by_hotel = {row[0]: row[2:] for row in table.rows}
    # Windows start at the snapshot, 2027-01-01: 7 and 30 days hold 4-5 Jan, 60 days adds 1 Feb.
    assert by_hotel["Target"][:4] == [210, 210, 240, 240]
    assert by_hotel["Comp A"][:3] == [150, 150, 183.33]
    assert by_hotel["Comp B"][:3] == [270, 270, 270]
    # Daily comp medians: 200 (4 Jan), 220 (5 Jan), 250 (1 Feb, Comp A only).
    assert table.total[:5] == ["Comp set median", None, 210, 210, 223.33]


def test_forward_vs_compset_uses_dates_both_priced() -> None:
    rows = {row[0]: row for row in forward_vs_compset(_position_data(), 1).rows}
    assert rows["Next 7 days"][2:] == [210, 210, 0.0, 2]
    assert rows["Next 60 days"][1] == D(2027, 1, 1)
    assert rows["Next 60 days"][2:] == [240, 223.33, 0.0746, 3]


def test_forward_points_count_priced_dates() -> None:
    rows = {row[0]: row[2:] for row in forward_points(_position_data(), 1).rows}
    assert rows["Comp B"][:3] == [2, 2, 2] and rows["Target"][:3] == [2, 2, 3]


# -- event response ----------------------------------------------------------------------------------------------------


def test_event_uplift_against_same_weekday_baseline() -> None:
    mondays = [D(2027, 1, 4), D(2027, 1, 11), D(2027, 1, 18), D(2027, 1, 25), D(2027, 2, 1)]
    data = _data(
        calendars={
            (1, 1): _calendar(TARGET, dict(zip(mondays, [200, 210, 300, 190, 300], strict=True))),
            (2, 1): _calendar(COMP_A, dict(zip(mondays, [100, 100, 150, 100, 250], strict=True))),
        },
        events=[EventInfo("Gala", D(2027, 1, 18), D(2027, 1, 18))],
    )
    table = event_response(data, 1)
    target_row, comp_row = table.rows
    assert target_row[1] == "Target" and target_row[3:8] == ["Target", 300, 225, 0.3333, None]
    assert comp_row[1] == "Comp A" and comp_row[3:8] == ["Comp", 150, 137.5, 0.0909, 0.2424]
    assert target_row[8] == comp_row[8] == 4


def test_event_baseline_skips_other_event_days() -> None:
    mondays = [D(2027, 1, 4), D(2027, 1, 11), D(2027, 1, 18), D(2027, 1, 25), D(2027, 2, 1)]
    data = _data(
        calendars={(1, 1): _calendar(TARGET, dict(zip(mondays, [200, 210, 300, 190, 300], strict=True)))},
        events=[EventInfo("Gala", D(2027, 1, 18), D(2027, 1, 18)), EventInfo("Other", D(2027, 1, 25), D(2027, 1, 25))],
    )
    gala = [row for row in event_response(data, 1).rows if row[0] == "Gala"]
    assert gala[0][5] == pytest.approx(236.67) and gala[0][8] == 3


# -- parity and room ladder --------------------------------------------------------------------------------------------


def _offers_data() -> ReportData:
    first, second = D(2027, 3, 1), D(2027, 3, 2)
    return _data(offers={TARGET.id: [
        _offer(first, "Target Hotel", 100, official=True, room="Standard", fc=True, breakfast=False),
        _offer(first, "Target Hotel", 90, official=True, room="Standard", fc=False, breakfast=False),
        _offer(first, "Target Hotel", 150, official=True, room="Deluxe", fc=False, breakfast=False),
        _offer(first, "Booking.com", 110, room="Standard", fc=True, breakfast=False),
        _offer(first, "Expedia", 95),
        _offer(second, "Target Hotel", 110, official=True, room="Standard", fc=False, breakfast=False),
        _offer(second, "Target Hotel", 160, official=True, room="Deluxe", fc=False, breakfast=False),
        _offer(second, "Booking.com", 95),
    ]})


def test_parity_compares_like_terms_when_rooms_are_listed() -> None:
    rows = {row[1]: row for row in parity(_offers_data()).rows}
    # Booking.com: on day 1 against the own site's free-cancellation 100 (+10%); on day 2 against 110 (-13.6%).
    assert rows["Booking.com"][2:] == [2, 105, 102.5, pytest.approx(-0.0182, abs=1e-4), 0.5]
    # Expedia lists no room, so it is compared with the own site's lowest price, 90.
    assert rows["Expedia"][2:] == [1, 90, 95, 0.0556, 1.0]
    # Own site cheapest of all channels on day 1 (90 vs 95), not on day 2 (110 vs 95).
    assert rows["All channels"][2] == 2 and rows["All channels"][6] == 0.5


def test_room_ladder_uses_typical_lowest_price() -> None:
    rows = room_ladder(_offers_data()).rows
    assert rows == [
        ["Standard", "Target", 1, 100, None, None, 2],
        ["Deluxe", "Target", 2, 155, 55, 0.55, 2],
    ]


# -- sell-outs and changes ---------------------------------------------------------------------------------------------


def test_sellout_signals_count_hotels_per_date() -> None:
    data = _data(calendars={
        (1, 1): _calendar(TARGET, {}, no_price=[D(2027, 2, 2)]),
        (2, 1): _calendar(COMP_A, {}, no_price=[D(2027, 2, 2)]),
        (3, 1): _calendar(COMP_B, {}, no_price=[D(2027, 2, 1)]),
    })
    table = sellouts(data, 1)
    assert table.rows == [
        [D(2027, 2, 1), "Comp B", "Monday", "Comp", 1],
        [D(2027, 2, 2), "Target", "Tuesday", "Target", 2],
        [D(2027, 2, 2), "Comp A", "Tuesday", "Comp", 2],
    ]


def _change_data() -> ReportData:
    previous = _calendar(TARGET, {D(2027, 1, 4): 180, D(2027, 1, 5): 220, D(2027, 2, 1): 300, D(2027, 2, 2): 310},
                         fetch_date=PREVIOUS)
    latest = _calendar(TARGET, {D(2027, 1, 4): 200, D(2027, 1, 5): 220, D(2027, 2, 1): 300},
                       no_price=[D(2027, 2, 2)])
    return _data(calendars={(1, 1): latest}, previous_calendars={(1, 1): previous})


def test_change_by_month() -> None:
    assert changes_by_month(_change_data(), 1).rows == [
        ["Target", "Jan 2027", 200, 210, 0.05, 2],
        ["Target", "Feb 2027", 300, 300, 0.0, 1],
    ]


def test_change_by_window_counts_newly_dark_dates() -> None:
    rows = {row[1]: row for row in changes_by_window(_change_data(), 1).rows}
    assert rows["Next 30 days"][4:] == [200, 210, 0.05, 2, 0, 0]
    # (180 + 220 + 300) / 3 = 233.33 before, (200 + 220 + 300) / 3 = 240 after; 2 Feb went dark.
    assert rows["Next 60 days"][4:] == [233.33, 240, 0.0286, 3, 1, 0]


# -- Excel -------------------------------------------------------------------------------------------------------------


def test_excel_export_follows_house_layout(tmp_path: Path) -> None:
    data = _position_data()
    data.offers = _offers_data().offers
    report = build_report(data)
    path = write_report(report, tmp_path / "report.xlsx")

    assert check_layout(path) == []
    sheet = load_workbook(path).active
    assert sheet["D3"].value == "Rate Shop Overview"
    assert sheet["D3"].fill.fgColor.rgb.endswith(HEADER_FILL)
    assert sheet["D3"].font.color.rgb.endswith("FFFFFF")
    assert sheet["E3"].alignment.horizontal == "centerContinuous"
    assert sheet["D4"].value == "Item" and sheet["D4"].fill.fgColor.rgb.endswith(SUBHEADER_FILL)
    assert sheet.column_dimensions["A"].width == xl_width(8) == 8.4140625
    assert sheet.column_dimensions["B"].width == sheet.column_dimensions["C"].width == 1.4140625
    totals = [row for row in sheet.iter_rows(min_col=4) if row[0].value == "All dates"]
    width = len(build_report(data).tables[2].columns)
    assert all(cell.fill.fgColor.rgb.endswith(TOTAL_FILL) for cell in totals[0][:width])

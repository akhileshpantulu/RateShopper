"""The report sections (docs/DESIGN.md, section 7) as plain functions over ReportData.

The screen and the Excel export render the same tables. Every table carries the number of data points behind each
figure (a "Dates" column), and the overview and coverage tables give the snapshot dates.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import mean, median
from typing import Any

from hotelrate.report.data import CalendarSnapshot, HotelInfo, OfferPoint, ReportData

WEEKDAYS = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")
EVENT_BASELINE_DAYS = 14
CHANGE_WINDOWS = (30, 60, 90)
FORWARD_WINDOWS = (7, 30, 60, 90, 180, 365)


@dataclass(frozen=True)
class Column:
    name: str
    kind: str = "text"  # text | date | money | pct (a change, signed) | share (a proportion) | int


@dataclass
class Table:
    title: str
    columns: list[Column]
    rows: list[list[Any]] = field(default_factory=list)
    total: list[Any] | None = None


@dataclass
class Report:
    title: str
    tables: list[Table]


def build_report(data: ReportData) -> Report:
    nights_list = data.calendar_nights or [1]
    main_nights = min(nights_list)
    tables = [overview(data), coverage(data)]
    for nights in nights_list:
        tables.append(forward_vs_compset(data, nights))
        tables.append(forward_averages(data, nights))
        tables.append(forward_points(data, nights))
    for nights in nights_list:
        tables.append(rate_position(data, nights, by="month"))
        tables.append(rate_position(data, nights, by="weekday"))
    tables.append(event_response(data, main_nights))
    tables.append(parity(data))
    tables.append(room_ladder(data))
    tables.append(sellouts(data, main_nights))
    for nights in nights_list:
        tables.append(changes_by_month(data, nights))
        tables.append(changes_by_window(data, nights))
    return Report(title=f"Rate Shop: {data.study_name}", tables=tables)


# -- overview and coverage ---------------------------------------------------------------------------------------------


def overview(data: ReportData) -> Table:
    calendar_days = [snapshot.fetch_date for snapshot in data.calendars.values()]
    offers_days = list(data.offers_dates.values())
    target = data.target
    rows: list[list[Any]] = [
        ["Study", data.study_name],
        ["City", data.city],
        ["Currency", data.currency],
        ["Adults", data.adults],
        ["Target", target.name if target else "none"],
        *([["Comp", hotel.name] for hotel in data.comps] or [["Comp", "none"]]),
        ["Calendar snapshots", _date_range(calendar_days)],
        ["Offers snapshots", _date_range(offers_days)],
        ["Events", len(data.events)],
        ["Collector", ", ".join(data.collector_versions) or "none"],
        ["Generated", data.generated_at.strftime("%Y-%m-%d %H:%M UTC")],
    ]
    return Table("Rate Shop Overview", [Column("Item"), Column("Value")], rows)


def coverage(data: ReportData) -> Table:
    nights_list = data.calendar_nights or [1]
    columns = [Column("Hotel"), Column("Role")]
    for nights in nights_list:
        columns += [Column(f"{nights}N snapshot", "date"), Column(f"{nights}N priced", "int"),
                    Column(f"{nights}N no price", "int")]
    columns += [Column("Horizon", "date"), Column("Offers snapshot", "date"), Column("Offers dates", "int"),
                Column("Channels", "int")]
    rows = []
    for hotel in data.hotels:
        row: list[Any] = [hotel.name, hotel.role.title()]
        for nights in nights_list:
            snapshot = data.calendars.get((hotel.id, nights))
            row += [snapshot.fetch_date if snapshot else None, len(snapshot.rates) if snapshot else None,
                    len(snapshot.no_price) if snapshot else None]
        main = data.calendars.get((hotel.id, min(nights_list)))
        offers = data.offers.get(hotel.id, [])
        row += [
            main.horizon if main else None,
            data.offers_dates.get(hotel.id),
            len({offer.check_in for offer in offers}) or None,
            len({offer.channel for offer in offers}) or None,
        ]
        rows.append(row)
    return Table("Data Coverage", columns, rows)


# -- forward averages -------------------------------------------------------------------------------------------------


def forward_as_of(data: ReportData, nights: int) -> date | None:
    """The day forward windows start: the latest calendar snapshot among the study's hotels."""
    days = [snapshot.fetch_date for (_, n), snapshot in data.calendars.items() if n == nights]
    return max(days, default=None)


def window_days(start: date, days: int) -> list[date]:
    return [start + timedelta(days=offset) for offset in range(days)]


def window_average(rates: dict[date, float], start: date, days: int) -> tuple[float | None, int]:
    """Average nightly rate over the priced check-in dates in [start, start + days), and how many there were."""
    values = [rates[day] for day in window_days(start, days) if day in rates]
    return (round(mean(values), 2) if values else None), len(values)


def comp_median_by_date(data: ReportData, nights: int) -> dict[date, float]:
    comp_rates = [_rates(data, comp.id, nights) for comp in data.comps]
    days = {day for rates in comp_rates for day in rates}
    return {
        day: median(rates[day] for rates in comp_rates if day in rates) for day in days
    }


def forward_averages(data: ReportData, nights: int) -> Table:
    columns = [Column("Hotel"), Column("Role")] + [Column(f"Next {days} days", "money") for days in FORWARD_WINDOWS]
    table = Table(f"Forward Average Rate ({_nights_label(nights)})", columns)
    start = forward_as_of(data, nights)
    if start is None:
        return table
    for hotel in data.hotels:
        rates = _rates(data, hotel.id, nights)
        table.rows.append([hotel.name, hotel.role.title(),
                           *(window_average(rates, start, days)[0] for days in FORWARD_WINDOWS)])
    if data.comps:
        medians = comp_median_by_date(data, nights)
        table.total = ["Comp set median", None, *(window_average(medians, start, days)[0] for days in FORWARD_WINDOWS)]
    return table


def forward_points(data: ReportData, nights: int) -> Table:
    columns = [Column("Hotel"), Column("Role")] + [Column(f"Next {days} days", "int") for days in FORWARD_WINDOWS]
    table = Table(f"Forward Average Priced Dates ({_nights_label(nights)})", columns)
    start = forward_as_of(data, nights)
    if start is None:
        return table
    for hotel in data.hotels:
        rates = _rates(data, hotel.id, nights)
        table.rows.append([hotel.name, hotel.role.title(),
                           *(window_average(rates, start, days)[1] for days in FORWARD_WINDOWS)])
    return table


def forward_vs_compset(data: ReportData, nights: int) -> Table:
    """Target against the comp-set median on the dates both are priced, for each forward window."""
    columns = [
        Column("Window"), Column("Snapshot", "date"), Column("Target", "money"), Column("Comp set median", "money"),
        Column("Target vs median", "pct"), Column("Dates", "int"),
    ]
    table = Table(f"Target vs Comp Set, Forward ({_nights_label(nights)})", columns)
    start, target = forward_as_of(data, nights), data.target
    if start is None or target is None or not data.comps:
        return table
    target_rates = _rates(data, target.id, nights)
    medians = comp_median_by_date(data, nights)
    for days in FORWARD_WINDOWS:
        matched = [day for day in window_days(start, days) if day in target_rates and day in medians]
        if not matched:
            table.rows.append([f"Next {days} days", start, None, None, None, 0])
            continue
        target_avg = mean(target_rates[day] for day in matched)
        median_avg = mean(medians[day] for day in matched)
        table.rows.append([f"Next {days} days", start, round(target_avg, 2), round(median_avg, 2),
                           _ratio(target_avg, median_avg), len(matched)])
    return table


# -- 1. rate position --------------------------------------------------------------------------------------------------


def rate_position(data: ReportData, nights: int, *, by: str) -> Table:
    label = "Month" if by == "month" else "Day of Week"
    columns = [
        Column(label), Column("Target", "money"), Column("Comp median", "money"), Column("Comp min", "money"),
        Column("Comp max", "money"), Column("Target vs median", "pct"), Column("Dates", "int"),
    ]
    title = f"Rate Position by {label} ({_nights_label(nights)})"
    table = Table(title, columns)
    target = data.target
    if target is None:
        return table
    target_rates = _rates(data, target.id, nights)
    comp_rates = [_rates(data, comp.id, nights) for comp in data.comps]
    points = []
    for day in sorted(target_rates):
        comps = [rates[day] for rates in comp_rates if day in rates]
        if comps:
            points.append((day, target_rates[day], comps))
    if not points:
        return table

    if by == "month":
        def key(day: date) -> tuple[int, int]:
            return (day.year, day.month)

        def name(group: tuple[int, int]) -> str:
            return date(group[0], group[1], 1).strftime("%b %Y")
    else:
        def key(day: date) -> int:
            return day.weekday()

        def name(group: int) -> str:
            return WEEKDAYS[group]

    groups: dict[Any, list[tuple[date, float, list[float]]]] = defaultdict(list)
    for point in points:
        groups[key(point[0])].append(point)
    table.rows = [[name(group), *_position_stats(groups[group])] for group in sorted(groups)]
    table.total = ["All dates", *_position_stats(points)]
    return table


def _position_stats(points: list[tuple[date, float, list[float]]]) -> list[Any]:
    target_avg = mean(point[1] for point in points)
    median_avg = mean(median(point[2]) for point in points)
    all_comps = [rate for point in points for rate in point[2]]
    return [
        round(target_avg, 2), round(median_avg, 2), round(min(all_comps), 2), round(max(all_comps), 2),
        _ratio(target_avg, median_avg), len(points),
    ]


# -- 2. event response -------------------------------------------------------------------------------------------------


def event_response(data: ReportData, nights: int) -> Table:
    columns = [
        Column("Event"), Column("Hotel"), Column("Date", "date"), Column("Role"), Column("Event rate", "money"),
        Column("Baseline", "money"), Column("Uplift", "pct"), Column("Gap to target", "pct"),
        Column("Baseline dates", "int"),
    ]
    table = Table(f"Event Response ({_nights_label(nights)})", columns)
    all_event_days = data.event_days
    for event in sorted(data.events, key=lambda item: (item.start, item.name)):
        for day in event.days:
            uplifts: dict[int, tuple[float, float, float, int]] = {}
            for hotel in data.hotels:
                rates = _rates(data, hotel.id, nights)
                if day not in rates:
                    continue
                baseline_days = [
                    day + timedelta(days=offset)
                    for offset in range(-EVENT_BASELINE_DAYS, EVENT_BASELINE_DAYS + 1, 7)
                    if offset != 0
                ]
                baseline = [rates[other] for other in baseline_days if other in rates and other not in all_event_days]
                if not baseline:
                    continue
                base = mean(baseline)
                uplifts[hotel.id] = (rates[day], base, rates[day] / base - 1, len(baseline))
            target = data.target
            target_uplift = uplifts.get(target.id)[2] if target and target.id in uplifts else None
            for hotel in data.hotels:
                if hotel.id not in uplifts:
                    continue
                rate, base, uplift, count = uplifts[hotel.id]
                gap = None if hotel.role == "target" or target_uplift is None else round(target_uplift - uplift, 4)
                table.rows.append([
                    event.name, hotel.name, day, hotel.role.title(), round(rate, 2), round(base, 2),
                    round(uplift, 4), gap, count,
                ])
    return table


# -- 3. parity ---------------------------------------------------------------------------------------------------------


def parity(data: ReportData) -> Table:
    columns = [
        Column("Hotel"), Column("Channel"), Column("Dates", "int"), Column("Own site avg", "money"),
        Column("Channel avg", "money"), Column("Channel vs own", "pct"), Column("Own site cheaper", "share"),
    ]
    table = Table("Rate Parity: Own Site vs Booking Channels", columns)
    for hotel in data.hotels:
        by_date: dict[date, list[OfferPoint]] = defaultdict(list)
        for offer in data.offers.get(hotel.id, []):
            by_date[offer.check_in].append(offer)
        pairs: dict[str, list[tuple[float, float]]] = defaultdict(list)
        cheapest_flags: list[bool] = []
        for offers in by_date.values():
            own_rows = [offer for offer in offers if offer.is_official_site]
            if not own_rows:
                continue
            channels: dict[str, list[OfferPoint]] = defaultdict(list)
            for offer in offers:
                if not offer.is_official_site:
                    channels[offer.channel].append(offer)
            if not channels:
                continue
            own_lowest = min(offer.price_per_night for offer in own_rows)
            cheapest_flags.append(
                own_lowest <= min(offer.price_per_night for rows in channels.values() for offer in rows)
            )
            for channel, rows in channels.items():
                best = min(rows, key=lambda offer: offer.price_per_night)
                pairs[channel].append((_comparable_own_price(own_rows, best), best.price_per_night))
        ordered = sorted(pairs.items(), key=lambda item: (-len(item[1]), item[0].lower()))
        for channel, values in ordered:
            own_avg = mean(own for own, _ in values)
            channel_avg = mean(other for _, other in values)
            table.rows.append([
                hotel.name, channel, len(values), round(own_avg, 2), round(channel_avg, 2),
                round(mean(other / own - 1 for own, other in values), 4),
                round(sum(other > own for own, other in values) / len(values), 4),
            ])
        if cheapest_flags:
            table.rows.append([
                hotel.name, "All channels", len(cheapest_flags), None, None, None,
                round(sum(cheapest_flags) / len(cheapest_flags), 4),
            ])
    return table


def _comparable_own_price(own_rows: list[OfferPoint], channel_offer: OfferPoint) -> float:
    """The own site's lowest price, on the same cancellation and breakfast terms when both sides list rooms."""
    if channel_offer.room is not None and channel_offer.free_cancellation is not None:
        same_terms = [
            offer.price_per_night
            for offer in own_rows
            if offer.room is not None
            and offer.free_cancellation == channel_offer.free_cancellation
            and offer.breakfast == channel_offer.breakfast
        ]
        if same_terms:
            return min(same_terms)
    return min(offer.price_per_night for offer in own_rows)


# -- 4. room ladder ----------------------------------------------------------------------------------------------------


def room_ladder(data: ReportData) -> Table:
    columns = [
        Column("Room"), Column("Hotel"), Column("Position", "int"), Column("Typical lowest", "money"),
        Column("Step", "money"), Column("Step %", "pct"), Column("Dates", "int"),
    ]
    table = Table("Room Ladder", columns)
    for hotel in data.hotels:
        rooms = [offer for offer in data.offers.get(hotel.id, []) if offer.room]
        official = [offer for offer in rooms if offer.is_official_site]
        source = official or rooms
        lowest: dict[str, dict[date, float]] = defaultdict(dict)
        for offer in source:
            current = lowest[offer.room].get(offer.check_in)
            if current is None or offer.price_per_night < current:
                lowest[offer.room][offer.check_in] = offer.price_per_night
        ladder = sorted(
            ((room, median(prices.values()), len(prices)) for room, prices in lowest.items()),
            key=lambda item: (item[1], item[0]),
        )
        previous: float | None = None
        for position, (room, price, count) in enumerate(ladder, start=1):
            step = None if previous is None else round(price - previous, 2)
            step_pct = None if previous is None else _ratio(price, previous)
            table.rows.append([room, hotel.name, position, round(price, 2), step, step_pct, count])
            previous = price
    return table


# -- 5. sell-out signals -----------------------------------------------------------------------------------------------


def sellouts(data: ReportData, nights: int) -> Table:
    columns = [
        Column("Date", "date"), Column("Hotel"), Column("Day"), Column("Role"), Column("Hotels without price", "int"),
    ]
    table = Table(f"Sell-Out Signals ({_nights_label(nights)})", columns)
    dark: dict[date, list[HotelInfo]] = defaultdict(list)
    for hotel in data.hotels:
        snapshot = data.calendars.get((hotel.id, nights))
        for day in snapshot.no_price if snapshot else ():
            dark[day].append(hotel)
    for day in sorted(dark):
        for hotel in dark[day]:
            table.rows.append([day, hotel.name, WEEKDAYS[day.weekday()], hotel.role.title(), len(dark[day])])
    return table


# -- 6. change since last run ------------------------------------------------------------------------------------------


def changes_by_month(data: ReportData, nights: int) -> Table:
    columns = [
        Column("Hotel"), Column("Month"), Column("Previous avg", "money"), Column("Latest avg", "money"),
        Column("Change", "pct"), Column("Dates", "int"),
    ]
    table = Table(f"Change Since Previous Snapshot by Month ({_nights_label(nights)})", columns)
    for hotel, previous, latest in _snapshot_pairs(data, nights):
        groups: dict[tuple[int, int], list[date]] = defaultdict(list)
        for day in _common_days(previous, latest):
            groups[(day.year, day.month)].append(day)
        for (year, month), days in sorted(groups.items()):
            before = mean(previous.rates[day] for day in days)
            after = mean(latest.rates[day] for day in days)
            table.rows.append([
                hotel.name, date(year, month, 1).strftime("%b %Y"), round(before, 2), round(after, 2),
                _ratio(after, before), len(days),
            ])
    return table


def changes_by_window(data: ReportData, nights: int) -> Table:
    columns = [
        Column("Hotel"), Column("Window"), Column("Previous", "date"), Column("Latest", "date"),
        Column("Previous avg", "money"), Column("Latest avg", "money"), Column("Change", "pct"),
        Column("Dates", "int"), Column("Newly no price", "int"), Column("Newly priced", "int"),
    ]
    table = Table(f"Change Since Previous Snapshot, Next 30/60/90 Days ({_nights_label(nights)})", columns)
    for hotel, previous, latest in _snapshot_pairs(data, nights):
        for window in CHANGE_WINDOWS:
            end = latest.fetch_date + timedelta(days=window - 1)

            def inside(day: date, end: date = end) -> bool:
                return latest.fetch_date <= day <= end

            days = [day for day in _common_days(previous, latest) if inside(day)]
            before = mean(previous.rates[day] for day in days) if days else None
            after = mean(latest.rates[day] for day in days) if days else None
            newly_dark = sum(1 for day in latest.no_price if inside(day) and day in previous.rates)
            newly_priced = sum(1 for day in latest.rates if inside(day) and day in previous.no_price)
            table.rows.append([
                hotel.name, f"Next {window} days", previous.fetch_date, latest.fetch_date,
                round(before, 2) if before is not None else None, round(after, 2) if after is not None else None,
                _ratio(after, before) if days else None, len(days), newly_dark, newly_priced,
            ])
    return table


def _snapshot_pairs(data: ReportData, nights: int) -> Iterable[tuple[HotelInfo, CalendarSnapshot, CalendarSnapshot]]:
    for hotel in data.hotels:
        latest = data.calendars.get((hotel.id, nights))
        previous = data.previous_calendars.get((hotel.id, nights))
        if latest is not None and previous is not None:
            yield hotel, previous, latest


def _common_days(previous: CalendarSnapshot, latest: CalendarSnapshot) -> list[date]:
    return sorted(day for day in latest.rates if day in previous.rates and day >= latest.fetch_date)


# -- helpers -----------------------------------------------------------------------------------------------------------


def _rates(data: ReportData, hotel_id: int, nights: int) -> dict[date, float]:
    snapshot = data.calendars.get((hotel_id, nights))
    return snapshot.rates if snapshot else {}


def _ratio(value: float | None, base: float | None) -> float | None:
    if value is None or not base:
        return None
    return round(value / base - 1, 4)


def _nights_label(nights: int) -> str:
    return "1 Night" if nights == 1 else f"{nights} Nights"


def _date_range(days: list[date]) -> str:
    if not days:
        return "none"
    first, last = min(days), max(days)
    return first.isoformat() if first == last else f"{first.isoformat()} to {last.isoformat()}"


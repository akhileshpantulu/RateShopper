"""Which check-in dates get a full channel-offers call (docs/DESIGN.md, section 6)."""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date, timedelta


def second_monday(year: int, month: int) -> date:
    first = date(year, month, 1)
    first_monday = first + timedelta(days=(7 - first.weekday()) % 7)
    return first_monday + timedelta(days=7)


def sample_dates(
    start: date,
    *,
    months: int = 12,
    horizon: date | None = None,
    extra: Iterable[date] = (),
) -> list[date]:
    """One full week, Monday to Sunday from the second Monday, for each of `months` months from `start`'s month,
    plus `extra` dates such as events. Dates before `start` or after `horizon` are dropped."""
    picked: set[date] = set(extra)
    year, month = start.year, start.month
    for _ in range(months):
        monday = second_monday(year, month)
        picked.update(monday + timedelta(days=offset) for offset in range(7))
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)
    return sorted(day for day in picked if day >= start and (horizon is None or day <= horizon))

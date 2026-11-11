"""What a report reads from the store: each hotel's latest (and previous) calendar and its latest offers."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hotelrate.store.models import Fetch, RateObservation, Study
from hotelrate.store.studies import event_days, study_events, study_hotels


@dataclass(frozen=True)
class HotelInfo:
    id: int
    name: str
    role: str


@dataclass
class CalendarSnapshot:
    hotel_id: int
    nights: int
    fetch_date: date
    rates: dict[date, float] = field(default_factory=dict)
    no_price: set[date] = field(default_factory=set)

    @property
    def horizon(self) -> date | None:
        return max(self.rates, default=None)


@dataclass(frozen=True)
class OfferPoint:
    hotel_id: int
    check_in: date
    channel: str
    is_official_site: bool
    is_sponsored: bool
    room: str | None
    free_cancellation: bool | None
    breakfast: bool | None
    price_per_night: float


@dataclass(frozen=True)
class EventInfo:
    name: str
    start: date
    end: date

    @property
    def days(self) -> list[date]:
        return [date.fromordinal(day) for day in range(self.start.toordinal(), self.end.toordinal() + 1)]


@dataclass
class ReportData:
    study_name: str
    city: str
    currency: str
    adults: int
    calendar_nights: list[int]
    offers_nights: int
    hotels: list[HotelInfo]
    calendars: dict[tuple[int, int], CalendarSnapshot] = field(default_factory=dict)
    previous_calendars: dict[tuple[int, int], CalendarSnapshot] = field(default_factory=dict)
    offers: dict[int, list[OfferPoint]] = field(default_factory=dict)
    offers_dates: dict[int, date] = field(default_factory=dict)
    events: list[EventInfo] = field(default_factory=list)
    collector_versions: list[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def target(self) -> HotelInfo | None:
        return next((hotel for hotel in self.hotels if hotel.role == "target"), None)

    @property
    def comps(self) -> list[HotelInfo]:
        return [hotel for hotel in self.hotels if hotel.role == "comp"]

    @property
    def event_days(self) -> set[date]:
        return {day for event in self.events for day in event.days}


def load_report_data(session: Session, study: Study) -> ReportData:
    members = study_hotels(session, study)
    data = ReportData(
        study_name=study.name,
        city=study.city,
        currency=study.currency,
        adults=study.adults,
        calendar_nights=sorted(study.calendar_nights),
        offers_nights=study.offers_nights,
        hotels=[HotelInfo(member.hotel.id, member.hotel.name, member.role) for member in members],
        events=[
            EventInfo(event.name, event.event_date, event.end_date or event.event_date)
            for event in study_events(session, study)
            if event_days(event)
        ],
    )
    versions: set[str] = set()
    for hotel in data.hotels:
        for nights in data.calendar_nights:
            fetches = list(
                session.scalars(
                    select(Fetch)
                    .where(
                        Fetch.hotel_id == hotel.id, Fetch.task == "calendar", Fetch.outcome == "ok",
                        Fetch.nights == nights, Fetch.adults == study.adults, Fetch.currency == study.currency,
                    )
                    .order_by(Fetch.fetch_date.desc(), Fetch.fetched_at.desc())
                )
            )
            if not fetches:
                continue
            latest = fetches[0]
            data.calendars[(hotel.id, nights)] = _calendar(session, latest)
            versions.add(latest.collector_version)
            previous = next((fetch for fetch in fetches if fetch.fetch_date < latest.fetch_date), None)
            if previous is not None:
                data.previous_calendars[(hotel.id, nights)] = _calendar(session, previous)

        offers_filter = (
            Fetch.hotel_id == hotel.id, Fetch.task == "offers", Fetch.outcome == "ok",
            Fetch.nights == study.offers_nights, Fetch.adults == study.adults, Fetch.currency == study.currency,
        )
        latest_day = session.scalar(select(func.max(Fetch.fetch_date)).where(*offers_filter))
        if latest_day is None:
            continue
        fetches = list(session.scalars(select(Fetch).where(*offers_filter, Fetch.fetch_date == latest_day)))
        versions.update(fetch.collector_version for fetch in fetches)
        rows = session.scalars(
            select(RateObservation).where(
                RateObservation.fetch_id.in_([fetch.id for fetch in fetches]),
                RateObservation.available.is_(True),
                RateObservation.price_per_night.is_not(None),
            )
        )
        data.offers[hotel.id] = [
            OfferPoint(
                hotel_id=hotel.id, check_in=row.check_in, channel=row.channel or "unknown",
                is_official_site=bool(row.is_official_site), is_sponsored=bool(row.is_sponsored), room=row.room,
                free_cancellation=row.free_cancellation, breakfast=row.breakfast,
                price_per_night=float(row.price_per_night),
            )
            for row in rows
        ]
        data.offers_dates[hotel.id] = latest_day
    data.collector_versions = sorted(versions)
    return data


def _calendar(session: Session, fetch: Fetch) -> CalendarSnapshot:
    snapshot = CalendarSnapshot(hotel_id=fetch.hotel_id, nights=fetch.nights, fetch_date=fetch.fetch_date)
    grouped: dict[str, list[RateObservation]] = defaultdict(list)
    for row in session.scalars(select(RateObservation).where(RateObservation.fetch_id == fetch.id)):
        grouped[row.day_status or ""].append(row)
    for row in grouped["priced"]:
        if row.price_per_night is not None:
            snapshot.rates[row.check_in] = float(row.price_per_night)
    snapshot.no_price = {row.check_in for row in grouped["no_price"]}
    return snapshot

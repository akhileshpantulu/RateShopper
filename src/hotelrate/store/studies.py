"""Studies, their hotels and city events."""

from __future__ import annotations

import csv
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from hotelrate.rateshop.google_hotels import COLLECTOR_NAME
from hotelrate.rateshop.models import HotelCandidate
from hotelrate.store.models import Event, Hotel, Run, Study, StudyHotel

CURRENCY_LENGTH = 3
EVENT_COLUMNS = ("city", "country", "event_date", "end_date", "name")


class StudyError(ValueError):
    """A study request that cannot be applied as given."""


def create_study(
    session: Session,
    name: str,
    *,
    city: str,
    currency: str,
    country: str | None = None,
    adults: int = 2,
    calendar_days: int = 365,
    calendar_nights: Iterable[int] = (1, 2),
    offers_nights: int = 1,
    offers_months: int = 12,
    created_by: str | None = None,
) -> Study:
    name = name.strip()
    if not name:
        raise StudyError("A study needs a name")
    if session.scalar(select(Study).where(Study.name == name)) is not None:
        raise StudyError(f"A study named {name!r} already exists")
    code = currency.strip().upper()
    if len(code) != CURRENCY_LENGTH or not code.isalpha():
        raise StudyError(f"currency must be a three-letter code such as GBP, got {currency!r}")
    nights = sorted(set(calendar_nights))
    if not nights or any(n < 1 or n > 30 for n in nights):
        raise StudyError("calendar nights must be between 1 and 30")
    if not 1 <= calendar_days <= 400:
        raise StudyError("calendar days must be between 1 and 400")
    study = Study(
        name=name,
        city=city.strip(),
        country=country.strip() if country else None,
        currency=code,
        adults=adults,
        calendar_days=calendar_days,
        calendar_nights=nights,
        offers_nights=offers_nights,
        offers_months=offers_months,
        schedule="none",
        status="active",
        created_by=created_by,
    )
    session.add(study)
    session.flush()
    return study


def get_study(session: Session, name_or_id: str | int) -> Study:
    if isinstance(name_or_id, int) or str(name_or_id).isdigit():
        study = session.get(Study, int(name_or_id))
    else:
        study = session.scalar(select(Study).where(Study.name == str(name_or_id).strip()))
    if study is None:
        raise StudyError(f"No study {name_or_id!r}")
    return study


def list_studies(session: Session) -> list[Study]:
    return list(session.scalars(select(Study).order_by(Study.name)))


def upsert_hotel(
    session: Session,
    *,
    source_id: str,
    name: str,
    collector: str = COLLECTOR_NAME,
    city: str | None = None,
    country: str | None = None,
    candidate: HotelCandidate | None = None,
) -> Hotel:
    hotel = session.scalar(select(Hotel).where(Hotel.collector == collector, Hotel.source_id == source_id))
    if hotel is None:
        hotel = Hotel(collector=collector, source_id=source_id, name=name)
        session.add(hotel)
    hotel.name = name or hotel.name
    hotel.city = city or hotel.city
    hotel.country = country or hotel.country
    if candidate is not None:
        hotel.star_class = candidate.hotel_class
        hotel.rating = candidate.rating
        hotel.reviews_count = candidate.reviews_count
        hotel.latitude = candidate.latitude
        hotel.longitude = candidate.longitude
        hotel.google_url = candidate.google_url
    session.flush()
    return hotel


def add_hotel_to_study(
    session: Session,
    study: Study,
    hotel: Hotel,
    *,
    role: str,
    input_name: str | None = None,
    confirmed_by: str | None = None,
) -> StudyHotel:
    if role not in ("target", "comp"):
        raise StudyError(f"role must be target or comp, got {role!r}")
    existing = session.get(StudyHotel, (study.id, hotel.id))
    if existing is not None:
        raise StudyError(f"{hotel.name} is already in {study.name} as {existing.role}")
    if role == "target" and any(member.role == "target" for member in study.members):
        raise StudyError(f"{study.name} already has a target hotel")
    member = StudyHotel(
        study=study,
        hotel=hotel,
        role=role,
        input_name=input_name,
        confirmed_by=confirmed_by,
        confirmed_at=datetime.now(timezone.utc),
    )
    session.add(member)
    session.flush()
    return member


def remove_hotel_from_study(session: Session, study: Study, hotel_id: int) -> None:
    """Take a hotel out of a study. Its stored observations stay; other studies may use them."""
    member = session.get(StudyHotel, (study.id, hotel_id))
    if member is None:
        raise StudyError(f"Hotel {hotel_id} is not in {study.name}")
    session.delete(member)
    session.flush()


def study_hotels(session: Session, study: Study) -> list[StudyHotel]:
    """Target first, then comps by name."""
    members = list(
        session.scalars(select(StudyHotel).where(StudyHotel.study_id == study.id).join(Hotel))
    )
    return sorted(members, key=lambda member: (member.role != "target", member.hotel.name.lower()))


def add_event(
    session: Session,
    *,
    city: str,
    event_date: date,
    name: str,
    end_date: date | None = None,
    country: str | None = None,
    created_by: str | None = None,
) -> Event:
    if end_date is not None and end_date < event_date:
        raise StudyError(f"{name}: end date {end_date} is before start date {event_date}")
    existing = session.scalar(
        select(Event).where(
            func.lower(Event.city) == city.strip().lower(), Event.event_date == event_date, Event.name == name.strip()
        )
    )
    if existing is not None:
        return existing
    event = Event(
        city=city.strip(),
        country=country.strip() if country else None,
        event_date=event_date,
        end_date=end_date,
        name=name.strip(),
        created_by=created_by,
    )
    session.add(event)
    session.flush()
    return event


@dataclass
class EventImport:
    added: int
    errors: list[str]


def import_events_csv(session: Session, path: Path, *, created_by: str | None = None) -> EventImport:
    """Columns: city, country, event_date, end_date, name (country and end_date may be blank).

    The whole file is checked first; nothing is added if any row is invalid.
    """
    rows: list[dict[str, object]] = []
    errors: list[str] = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        reader = csv.DictReader(handle)
        missing = [column for column in ("city", "event_date", "name") if column not in (reader.fieldnames or [])]
        if missing:
            return EventImport(0, [f"missing columns: {', '.join(missing)}"])
        for line, row in enumerate(reader, start=2):
            try:
                start = date.fromisoformat((row.get("event_date") or "").strip())
                end_text = (row.get("end_date") or "").strip()
                end = date.fromisoformat(end_text) if end_text else None
                if end is not None and end < start:
                    raise ValueError("end_date is before event_date")
                city = (row.get("city") or "").strip()
                name = (row.get("name") or "").strip()
                if not city or not name:
                    raise ValueError("city and name are required")
            except ValueError as error:
                errors.append(f"line {line}: {error}")
                continue
            rows.append({"city": city, "country": (row.get("country") or "").strip() or None,
                         "event_date": start, "end_date": end, "name": name})
    if errors:
        return EventImport(0, errors)
    for row in rows:
        add_event(session, created_by=created_by, **row)  # type: ignore[arg-type]
    return EventImport(len(rows), [])


def study_events(session: Session, study: Study) -> list[Event]:
    """Events in the study's city, by date."""
    return list(
        session.scalars(
            select(Event).where(func.lower(Event.city) == study.city.lower()).order_by(Event.event_date, Event.name)
        )
    )


def event_days(event: Event) -> list[date]:
    end = event.end_date or event.event_date
    return [date.fromordinal(day) for day in range(event.event_date.toordinal(), end.toordinal() + 1)]


def study_runs(session: Session, study: Study, limit: int = 10) -> list[Run]:
    return list(
        session.scalars(select(Run).where(Run.study_id == study.id).order_by(Run.id.desc()).limit(limit))
    )

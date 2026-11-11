"""Rate-shop store: schema, append-only observations, study rules, and full runs against recorded Google answers.

Runs on SQLite. Set TEST_DATABASE_URL (e.g. postgresql+psycopg://postgres:postgres@localhost:5432/rateshop_test)
to also run every test against PostgreSQL; that database is wiped.
"""

from __future__ import annotations

import os
from datetime import date
from pathlib import Path

import httpx
import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError

from hotelrate.config import Settings
from hotelrate.rateshop import GoogleHotelsCollector, PolicyConfig, RequestPolicy
from hotelrate.report import build_report, check_layout, load_report_data, write_report
from hotelrate.store import (
    Base,
    Fetch,
    RateObservation,
    RawResponse,
    Run,
    RunFetch,
    StudyError,
    StudyRunner,
    add_event,
    add_hotel_to_study,
    create_study,
    get_study,
    import_events_csv,
    make_engine,
    session_factory,
    session_scope,
    upgrade,
    upsert_hotel,
)
from tests.test_google_rateshop import SAVOY, STRAND_PALACE, TODAY, Recorder, _replay, _rpc_id

DATABASES = ["sqlite"] + (["postgresql"] if os.environ.get("TEST_DATABASE_URL") else [])


@pytest.fixture(params=DATABASES)
def engine(request, tmp_path: Path):
    if request.param == "sqlite":
        engine = make_engine(f"sqlite:///{tmp_path / 'store.db'}")
    else:
        engine = make_engine(os.environ["TEST_DATABASE_URL"])
        with engine.begin() as connection:
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
    upgrade(engine)
    yield engine
    engine.dispose()


@pytest.fixture
def factory(engine):
    return session_factory(engine)


def _collector(handler) -> GoogleHotelsCollector:
    policy = RequestPolicy(PolicyConfig(max_in_flight=2, pause_min_seconds=0, pause_max_seconds=0, max_retries=0,
                                        retry_base_seconds=0, block_pause_seconds=0))
    settings = Settings(_env_file=None, google_hotels_country="gb", default_currency="GBP")
    return GoogleHotelsCollector(settings, policy=policy, http_transport=httpx.MockTransport(handler),
                                 today=lambda: TODAY)


def _study(factory, *, offers_months: int = 1) -> int:
    with session_scope(factory) as session:
        study = create_study(session, "London Strand", city="London", currency="GBP", calendar_nights=[1, 2],
                             offers_months=offers_months)
        savoy = upsert_hotel(session, source_id=SAVOY, name="The Savoy", city="London")
        strand = upsert_hotel(session, source_id=STRAND_PALACE, name="Strand Palace", city="London")
        add_hotel_to_study(session, study, savoy, role="target", input_name="The Savoy")
        add_hotel_to_study(session, study, strand, role="comp", input_name="Strand Palace")
        add_event(session, city="london", event_date=date(2026, 11, 20), name="Trade fair")
        return study.id


async def _run(factory, handler, study_id: int, **options):
    async with _collector(handler) as collector:
        return await StudyRunner(factory, collector, today=TODAY, **options).run(study_id)


# -- schema ------------------------------------------------------------------------------------------------------------


def test_migration_builds_the_modelled_schema(engine) -> None:
    with engine.connect() as connection:
        context = MigrationContext.configure(connection, opts={"compare_type": True})
        assert compare_metadata(context, Base.metadata) == []


def test_rate_observations_are_append_only(factory) -> None:
    with session_scope(factory) as session:
        hotel = upsert_hotel(session, source_id=SAVOY, name="The Savoy")
        fetch = Fetch(fetch_key="k", collector="google_hotels", collector_version="v", hotel_id=hotel.id,
                      task="calendar", check_in=TODAY, nights=1, adults=2, currency="GBP", fetch_date=TODAY,
                      outcome="ok", attempts=1, requests=1)
        session.add(fetch)
        session.flush()
        session.add(RateObservation(hotel_id=hotel.id, fetch_id=fetch.id, source_task="calendar", check_in=TODAY,
                                    nights=1, adults=2, currency="GBP", available=True, price_per_night=100,
                                    observed_at=fetch.claimed_at))

    for statement in ("UPDATE rate_observations SET price_per_night = 1", "DELETE FROM rate_observations"):
        with pytest.raises((IntegrityError, DBAPIError), match="append-only"):
            with factory() as session, session.begin():
                session.execute(text(statement))


# -- studies -----------------------------------------------------------------------------------------------------------


def test_study_rules(factory) -> None:
    with session_scope(factory) as session:
        study = create_study(session, "Test", city="London", currency="gbp")
        assert study.currency == "GBP"
        with pytest.raises(StudyError, match="already exists"):
            create_study(session, "Test", city="London", currency="GBP")
        with pytest.raises(StudyError, match="three-letter"):
            create_study(session, "Other", city="London", currency="pounds")
        first = upsert_hotel(session, source_id="a", name="A")
        second = upsert_hotel(session, source_id="b", name="B")
        add_hotel_to_study(session, study, first, role="target")
        with pytest.raises(StudyError, match="already has a target"):
            add_hotel_to_study(session, study, second, role="target")
        with pytest.raises(StudyError, match="already in"):
            add_hotel_to_study(session, study, first, role="comp")
        assert get_study(session, "Test").id == get_study(session, str(study.id)).id


def test_search_details_never_erase_a_stored_position(factory) -> None:
    from hotelrate.rateshop import HotelCandidate, HotelLocation
    from hotelrate.store import set_hotel_location

    with session_scope(factory) as session:
        set_hotel_location(session, HotelLocation(source_id=SAVOY, name="The Savoy", latitude=51.51, longitude=-0.12,
                                                  address="Strand"))
        hotel = upsert_hotel(session, source_id=SAVOY, name="The Savoy",
                             candidate=HotelCandidate(source_id=SAVOY, name="The Savoy", rating=4.7))
        assert (hotel.latitude, hotel.longitude, hotel.address, hotel.rating) == (51.51, -0.12, "Strand", 4.7)


def test_event_import_is_all_or_nothing(factory, tmp_path: Path) -> None:
    good = tmp_path / "good.csv"
    good.write_text("city,country,event_date,end_date,name\nLondon,UK,2026-12-31,,New Year's Eve\n"
                    "London,UK,2027-07-10,2027-07-12,Festival\n")
    bad = tmp_path / "bad.csv"
    bad.write_text("city,country,event_date,end_date,name\nLondon,UK,2026-12-31,,Fine\nLondon,UK,31/12/2026,,Bad\n")
    with session_scope(factory) as session:
        assert import_events_csv(session, good).added == 2
        failed = import_events_csv(session, bad)
        assert failed.added == 0 and "line 3" in failed.errors[0]
        # Importing the same file again adds nothing new.
        import_events_csv(session, good)
        from hotelrate.store import Event

        assert session.scalar(select(func.count()).select_from(Event)) == 2


# -- runs --------------------------------------------------------------------------------------------------------------


async def test_run_stores_calendars_and_offers_then_reuses_them_the_same_day(factory, tmp_path: Path) -> None:
    study_id = _study(factory)
    recorder = Recorder()
    summary = await _run(factory, recorder, study_id)

    # 2 hotels x 2 calendars; offers on Nov 10-15 (rest of the sample week from today) plus the event on Nov 20.
    assert summary.status == "succeeded"
    assert summary.counts == {"calendar": {"ok": 4}, "offers": {"ok": 14}}
    assert summary.requests_sent == 18 == len(recorder.requests)
    with session_scope(factory) as session:
        calendar_rows = session.scalar(select(func.count()).where(RateObservation.source_task == "calendar"))
        assert calendar_rows == 4 * 365
        offer_rows = session.scalar(select(func.count()).where(RateObservation.source_task == "offers"))
        assert offer_rows >= 14 * 20
        assert session.scalar(select(func.count()).select_from(RawResponse)) == 0
        assert session.get(Run, summary.run_id).status == "succeeded"

    again = Recorder()
    second = await _run(factory, again, study_id)
    assert second.counts == {"calendar": {"reused": 4}, "offers": {"reused": 14}}
    assert second.requests_sent == 0 and not again.requests
    with session_scope(factory) as session:
        linked = session.scalar(select(func.count()).where(RunFetch.run_id == second.run_id, RunFetch.reused))
        assert linked == 18

    # The report reads what was stored. Both hotels replay the same calendar, so the target sits at the median.
    with session_scope(factory) as session:
        data = load_report_data(session, get_study(session, study_id))
    report = build_report(data)
    position = next(table for table in report.tables if table.title == "Rate Position by Month (1 Night)")
    assert position.total[5] == 0.0 and position.total[6] == 300
    path = write_report(report, tmp_path / "report.xlsx")
    assert check_layout(path) == []


async def test_failed_fetches_keep_raw_answers_and_are_retried_later_the_same_day(factory) -> None:
    study_id = _study(factory)

    def offers_blocked(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, text="slow down") if _rpc_id(request) == "lvkpBc" else _replay(request)

    first = await _run(factory, offers_blocked, study_id)
    assert first.status == "partial"
    assert first.counts == {"calendar": {"ok": 4}, "offers": {"blocked": 14}}
    with session_scope(factory) as session:
        raw = list(session.scalars(select(RawResponse)))
        assert len(raw) == 14 and {row.http_status for row in raw} == {429}
        assert session.scalar(select(func.count()).where(Fetch.outcome == "blocked")) == 14

    recorder = Recorder()
    second = await _run(factory, recorder, study_id)
    assert second.status == "succeeded"
    assert second.counts == {"calendar": {"reused": 4}, "offers": {"ok": 14}}
    assert {_rpc_id(request) for request in recorder.requests} == {"lvkpBc"}
    with session_scope(factory) as session:
        attempts = set(session.scalars(select(Fetch.attempts).where(Fetch.task == "offers")))
        assert attempts == {2}


async def test_calendar_only_run(factory) -> None:
    study_id = _study(factory)
    async with _collector(Recorder()) as collector:
        summary = await StudyRunner(factory, collector, today=TODAY).run(study_id, calendar_only=True)
    assert summary.counts == {"calendar": {"ok": 4}, "offers": {}}

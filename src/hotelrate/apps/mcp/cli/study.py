"""Study commands: set up a compset, record events, run collection, produce the report.

    hotelrate-mcp db upgrade
    hotelrate-mcp study create "London Strand" --city London --currency GBP
    hotelrate-mcp study add-hotel "London Strand" --role target --search "The Savoy" --city London
    hotelrate-mcp study add-hotel "London Strand" --role target --search "The Savoy" --city London --pick 1
    hotelrate-mcp event add --city London --date 2026-12-31 --name "New Year's Eve"
    hotelrate-mcp run "London Strand"
    hotelrate-mcp report "London Strand"
"""

from __future__ import annotations

import argparse
import asyncio
import re
import sys
from datetime import date
from pathlib import Path

from hotelrate.config import Settings, get_settings
from hotelrate.rateshop import CollectorError, GoogleHotelsCollector
from hotelrate.report import build_report, load_report_data, render_text, write_report
from hotelrate.store import (
    SchemaOutOfDateError,
    StudyError,
    StudyRunner,
    add_event,
    add_hotel_to_study,
    check_schema,
    create_study,
    get_study,
    import_events_csv,
    list_studies,
    make_engine,
    session_factory,
    session_scope,
    study_events,
    study_hotels,
    study_runs,
    upgrade,
    upsert_hotel,
)
from hotelrate.store.db import current_revision

REPORTS_DIR = Path("data/reports")


def add_commands(sub: argparse._SubParsersAction) -> None:
    db = sub.add_parser("db", help="Rate-shop database").add_subparsers(dest="db_command", required=True)
    db.add_parser("upgrade", help="Create or update the database schema").set_defaults(handler=cmd_db_upgrade)

    study = sub.add_parser("study", help="Create and inspect studies").add_subparsers(dest="study_command",
                                                                                     required=True)
    create = study.add_parser("create", help="Create a study")
    create.add_argument("name")
    create.add_argument("--city", required=True, help="City of the compset; events in this city are sampled")
    create.add_argument("--currency", required=True, help="ISO 4217 code, e.g. GBP")
    create.add_argument("--country", default=None)
    create.add_argument("--adults", type=int, default=2)
    create.add_argument("--calendar-days", type=int, default=365)
    create.add_argument("--calendar-nights", type=int, nargs="+", default=[1, 2])
    create.add_argument("--offers-nights", type=int, default=1)
    create.add_argument("--offers-months", type=int, default=12,
                        help="Months of sample weeks for channel offers (0 for calendar only)")
    create.add_argument("--created-by", default=None)
    create.set_defaults(handler=cmd_study_create)

    add = study.add_parser("add-hotel", help="Add the target or a comp hotel to a study")
    add.add_argument("study")
    add.add_argument("--role", choices=["target", "comp"], required=True)
    add.add_argument("--search", help="Hotel name to look up on Google Hotels")
    add.add_argument("--city", default="", help="City, to narrow --search")
    add.add_argument("--pick", type=int, help="Number of the search result to add, as listed without --pick")
    add.add_argument("--source-id", help="Google property token, instead of --search")
    add.add_argument("--name", help="Hotel name to store with --source-id")
    add.add_argument("--confirmed-by", default=None)
    add.set_defaults(handler=cmd_study_add_hotel)

    show = study.add_parser("show", help="Show a study's hotels, events and recent runs")
    show.add_argument("study")
    show.set_defaults(handler=cmd_study_show)
    study.add_parser("list", help="List studies").set_defaults(handler=cmd_study_list)

    event = sub.add_parser("event", help="City events sampled for channel offers").add_subparsers(
        dest="event_command", required=True
    )
    event_add = event.add_parser("add", help="Add one event")
    event_add.add_argument("--city", required=True)
    event_add.add_argument("--date", type=date.fromisoformat, required=True)
    event_add.add_argument("--end-date", type=date.fromisoformat, default=None)
    event_add.add_argument("--name", required=True)
    event_add.add_argument("--country", default=None)
    event_add.set_defaults(handler=cmd_event_add)
    event_import = event.add_parser("import", help="Import events from CSV: city,country,event_date,end_date,name")
    event_import.add_argument("file", type=Path)
    event_import.set_defaults(handler=cmd_event_import)

    run = sub.add_parser("run", help="Collect calendars and channel offers for every hotel in a study")
    run.add_argument("study")
    run.add_argument("--calendar-only", action="store_true", help="Skip channel offers (about a minute per study)")
    run.add_argument("--location", default=None, help="Label for where this run collected from, e.g. office")
    run.set_defaults(handler=cmd_run)

    report = sub.add_parser("report", help="Build the competitive pricing report and Excel export")
    report.add_argument("study")
    report.add_argument("--xlsx", type=Path, default=None, help=f"Excel path (default {REPORTS_DIR}/<study>_<date>.xlsx)")
    report.add_argument("--all-rows", action="store_true", help="Print every row on screen")
    report.set_defaults(handler=cmd_report)

    web = sub.add_parser("web", help="Open the browser front end (search, compsets, forward view)")
    web.add_argument("--host", default="127.0.0.1",
                     help="Address to listen on (default this computer only; there is no sign-in)")
    web.add_argument("--port", type=int, default=8765)
    web.add_argument("--open", action="store_true", help="Open the page in the default browser")
    web.set_defaults(handler=cmd_web)


# -- handlers ----------------------------------------------------------------------------------------------------------


def cmd_db_upgrade(args: argparse.Namespace) -> None:
    settings = get_settings()
    engine = make_engine(settings.store_database_url)
    before = current_revision(engine)
    upgrade(engine)
    after = current_revision(engine)
    url = engine.url.render_as_string(hide_password=True)
    print(f"{url}: schema {before or 'none'} -> {after}" if before != after else f"{url}: schema {after}, up to date")


def cmd_study_create(args: argparse.Namespace) -> None:
    factory, _settings = _open()
    with session_scope(factory) as session:
        study = _guard(lambda: create_study(
            session, args.name, city=args.city, currency=args.currency, country=args.country, adults=args.adults,
            calendar_days=args.calendar_days, calendar_nights=args.calendar_nights, offers_nights=args.offers_nights,
            offers_months=args.offers_months, created_by=args.created_by,
        ))
        print(f"Created study {study.id}: {study.name} ({study.city}, {study.currency})")
    print(f'Next: hotelrate-mcp study add-hotel "{args.name}" --role target --search "<hotel>" --city "{args.city}"')


def cmd_study_add_hotel(args: argparse.Namespace) -> None:
    factory, settings = _open()
    if bool(args.search) == bool(args.source_id):
        _fail("give either --search or --source-id")
    with session_scope(factory) as session:
        study = _guard(lambda: get_study(session, args.study))
        candidate = None
        if args.source_id:
            if not args.name:
                _fail("--source-id needs --name")
            source_id, name, input_name = args.source_id, args.name, args.name
        else:
            candidates = asyncio.run(_search(settings, args.search, args.city or study.city))
            if not candidates:
                _fail(f"Google Hotels found nothing for {args.search!r}")
            if args.pick is None:
                print(f"Candidates for {args.search!r}:")
                for number, found in enumerate(candidates, start=1):
                    stars = f"{found.hotel_class}*" if found.hotel_class else "-"
                    rating = f"{found.rating} ({found.reviews_count} reviews)" if found.rating else "no rating"
                    print(f"  {number}. {found.name}  {stars}  {rating}  {found.source_id}")
                print("Check the right property, then re-run with --pick <number> to add it.")
                return
            if not 1 <= args.pick <= len(candidates):
                _fail(f"--pick must be from 1 to {len(candidates)}")
            candidate = candidates[args.pick - 1]
            source_id, name, input_name = candidate.source_id, candidate.name or args.search, args.search
        hotel = upsert_hotel(session, source_id=source_id, name=name, city=study.city, country=study.country,
                             candidate=candidate)
        _guard(lambda: add_hotel_to_study(session, study, hotel, role=args.role, input_name=input_name,
                                          confirmed_by=args.confirmed_by))
        print(f"Added {hotel.name} to {study.name} as {args.role}")


def cmd_study_show(args: argparse.Namespace) -> None:
    factory, _settings = _open()
    with session_scope(factory) as session:
        study = _guard(lambda: get_study(session, args.study))
        print(f"{study.name} (id {study.id}): {study.city}, {study.currency}, {study.adults} adults")
        print(f"  calendar: {study.calendar_days} days, nights {study.calendar_nights}; "
              f"offers: {study.offers_nights} night(s), {study.offers_months} months of sample weeks")
        print("  hotels:")
        for member in study_hotels(session, study) or []:
            print(f"    {member.role:<6} {member.hotel.name}  {member.hotel.source_id}")
        events = study_events(session, study)
        print(f"  events in {study.city}: {len(events)}")
        for event in events[:10]:
            end = f" to {event.end_date}" if event.end_date else ""
            print(f"    {event.event_date}{end}  {event.name}")
        print("  recent runs:")
        for run in study_runs(session, study):
            print(f"    #{run.id} {run.started_at:%Y-%m-%d %H:%M} {run.status:<9} requests={run.requests_sent} "
                  f"{run.counts}")


def cmd_study_list(args: argparse.Namespace) -> None:
    factory, _settings = _open()
    with session_scope(factory) as session:
        for study in list_studies(session):
            print(f"{study.id:>4}  {study.name}  ({study.city}, {study.currency}, {len(study.members)} hotels)")


def cmd_event_add(args: argparse.Namespace) -> None:
    factory, _settings = _open()
    with session_scope(factory) as session:
        event = _guard(lambda: add_event(session, city=args.city, event_date=args.date, end_date=args.end_date,
                                         name=args.name, country=args.country))
        print(f"Event {event.id}: {event.name} on {event.event_date} in {event.city}")


def cmd_event_import(args: argparse.Namespace) -> None:
    factory, _settings = _open()
    with session_scope(factory) as session:
        result = import_events_csv(session, args.file)
    if result.errors:
        print("Nothing imported:", file=sys.stderr)
        for error in result.errors:
            print(f"  {error}", file=sys.stderr)
        raise SystemExit(1)
    print(f"Imported {result.added} events")


def cmd_run(args: argparse.Namespace) -> None:
    factory, settings = _open()
    with session_scope(factory) as session:
        study = _guard(lambda: get_study(session, args.study))
        study_id, name, hotels = study.id, study.name, len(study.members)
    if not hotels:
        _fail(f"{name} has no hotels; add them with: hotelrate-mcp study add-hotel")

    async def progress(message: str) -> None:
        print(f"  {message}", file=sys.stderr)

    async def main():
        async with GoogleHotelsCollector(settings) as collector:
            runner = StudyRunner(factory, collector, timezone_name=settings.store_timezone,
                                 raw_mode=settings.store_raw_responses, on_progress=progress)
            return await runner.run(study_id, location=args.location, calendar_only=args.calendar_only)

    print(f"Running {name}: {hotels} hotels", file=sys.stderr)
    summary = asyncio.run(main())
    print(f"Run #{summary.run_id}: {summary.status} in {summary.elapsed_seconds:.0f}s, "
          f"{summary.requests_sent} requests")
    for task, counts in summary.counts.items():
        if counts:
            print(f"  {task}: " + ", ".join(f"{key} {value}" for key, value in sorted(counts.items())))
    for error in summary.errors[:10]:
        print(f"  ! {error}", file=sys.stderr)
    print(f'Next: hotelrate-mcp report "{name}"')


def cmd_report(args: argparse.Namespace) -> None:
    factory, _settings = _open()
    with session_scope(factory) as session:
        study = _guard(lambda: get_study(session, args.study))
        data = load_report_data(session, study)
    report = build_report(data)
    print(render_text(report, max_rows=None if args.all_rows else 40))
    path = args.xlsx or REPORTS_DIR / f"{_slug(data.study_name)}_{date.today().isoformat()}.xlsx"
    write_report(report, path)
    print(f"Excel: {path}")


def cmd_web(args: argparse.Namespace) -> None:
    import threading
    import webbrowser

    import uvicorn

    from hotelrate.apps.web import create_app

    url = f"http://{'127.0.0.1' if args.host in ('0.0.0.0', '::') else args.host}:{args.port}"
    if args.host not in ("127.0.0.1", "localhost", "::1"):
        print(f"warning: listening on {args.host}; anyone who can reach this address can use the app",
              file=sys.stderr)
    print(f"Rate Shopper: {url}  (Ctrl+C to stop)")
    if args.open:
        threading.Timer(1.0, webbrowser.open, args=(url,)).start()
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="warning")


# -- helpers -----------------------------------------------------------------------------------------------------------


def _open():
    settings: Settings = get_settings()
    engine = make_engine(settings.store_database_url)
    if current_revision(engine) is None:
        print("Creating the rate-shop database schema", file=sys.stderr)
        upgrade(engine)
    try:
        check_schema(engine)
    except SchemaOutOfDateError as error:
        _fail(str(error))
    return session_factory(engine), settings


async def _search(settings: Settings, name: str, city: str):
    async with GoogleHotelsCollector(settings) as collector:
        try:
            return await collector.resolve_hotel(name, city, max_candidates=5)
        except CollectorError as error:
            _fail(f"{type(error).__name__}: {error}")


def _guard(action):
    try:
        return action()
    except StudyError as error:
        _fail(str(error))


def _fail(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(1)


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "study"

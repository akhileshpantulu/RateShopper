"""Local web app: search hotels, manage compsets, collect rates and read the forward view in a browser.

    hotelrate-mcp web            then open http://127.0.0.1:8765

There is no sign-in, so it listens on this computer only by default. Serving it to colleagues needs the
authenticated deployment planned for phase 4 (docs/DESIGN.md).
"""

from __future__ import annotations

import asyncio
import logging
import re
import tempfile
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session, sessionmaker
from starlette.background import BackgroundTask

from hotelrate.config import Settings, get_settings
from hotelrate.rateshop import CollectorError, DayStatus, GoogleHotelsCollector, HotelCandidate, NotFound
from hotelrate.report import build_report, load_report_data, write_report
from hotelrate.report.data import ReportData
from hotelrate.report.engine import (
    FORWARD_WINDOWS,
    Table,
    comp_median_by_date,
    forward_as_of,
    forward_averages,
    forward_vs_compset,
    window_average,
    window_days,
)
from hotelrate.store import (
    Run,
    StudyError,
    StudyRunner,
    add_event,
    add_hotel_to_study,
    check_schema,
    create_study,
    get_study,
    list_studies,
    make_engine,
    remove_hotel_from_study,
    session_factory,
    session_scope,
    study_events,
    study_hotels,
    study_runs,
    upgrade,
    upsert_hotel,
)
from hotelrate.store.db import current_revision

logger = logging.getLogger(__name__)
STATIC = Path(__file__).parent / "static"
MAX_MESSAGES = 200


# -- request bodies ----------------------------------------------------------------------------------------------------


class StudyIn(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    city: str = Field(min_length=1, max_length=120)
    currency: str = Field(min_length=3, max_length=3)
    country: str | None = None
    adults: int = Field(default=2, ge=1, le=8)
    calendar_nights: list[int] = Field(default_factory=lambda: [1, 2])
    offers_months: int = Field(default=12, ge=0, le=12)


class HotelIn(BaseModel):
    source_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    role: str = Field(pattern="^(target|comp)$")
    hotel_class: int | None = None
    rating: float | None = None
    reviews_count: int | None = None
    google_url: str | None = None


class RunIn(BaseModel):
    calendar_only: bool = False


class EventIn(BaseModel):
    city: str = Field(min_length=1)
    event_date: date
    end_date: date | None = None
    name: str = Field(min_length=1)
    country: str | None = None


# -- background runs ---------------------------------------------------------------------------------------------------


@dataclass
class LiveRun:
    study_id: int
    task: asyncio.Task | None = None
    messages: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_MESSAGES))
    error: str | None = None

    @property
    def active(self) -> bool:
        return self.task is not None and not self.task.done()


# -- app ---------------------------------------------------------------------------------------------------------------


def create_app(
    settings: Settings | None = None,
    *,
    factory: sessionmaker[Session] | None = None,
    collector: GoogleHotelsCollector | None = None,
    today: date | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    if factory is None:
        engine = make_engine(settings.store_database_url)
        if current_revision(engine) is None:
            upgrade(engine)
        check_schema(engine)
        factory = session_factory(engine)
    live_runs: dict[int, LiveRun] = {}
    shared: dict[str, GoogleHotelsCollector] = {}

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        shared["google"] = collector or GoogleHotelsCollector(settings)
        try:
            yield
        finally:
            for live in live_runs.values():
                if live.active:
                    live.task.cancel()
            if collector is None:
                await shared["google"].aclose()

    app = FastAPI(title="Rate Shopper", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    def google() -> GoogleHotelsCollector:
        return shared["google"]

    # -- pages and config -------------------------------------------------------------------------------------------

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/config")
    async def config() -> dict[str, Any]:
        return {
            "currency": settings.default_currency,
            "country": settings.google_hotels_country,
            "forward_windows": list(FORWARD_WINDOWS),
            "seconds_per_request": round((settings.rateshop_pause_min_seconds + settings.rateshop_pause_max_seconds) / 2
                                         + 1, 1),
        }

    # -- hotels -------------------------------------------------------------------------------------------------------

    @app.get("/api/search")
    async def search(q: str = Query(min_length=1), city: str = "", limit: int = Query(8, ge=1, le=20)):
        try:
            found = await google().resolve_hotel(q, city, max_candidates=limit)
        except CollectorError as error:
            raise _source_error(error) from error
        return {"candidates": [candidate.model_dump(mode="json") for candidate in found]}

    @app.get("/api/hotels/{source_id}/calendar")
    async def hotel_calendar(
        source_id: str,
        nights: int = Query(1, ge=1, le=30),
        days: int = Query(365, ge=1, le=400),
        currency: str | None = None,
    ):
        try:
            result = await google().price_calendar(source_id, days=days, nights=nights, currency=currency)
        except CollectorError as error:
            raise _source_error(error) from error
        rates = {day.check_in: day.rate_per_night for day in result.days
                 if day.status is DayStatus.PRICED and day.rate_per_night is not None}
        return {
            "hotel_id": result.hotel_id,
            "currency": result.currency,
            "nights": nights,
            "start": result.start.isoformat(),
            "horizon": result.horizon.isoformat() if result.horizon else None,
            "priced_days": result.priced_days,
            "no_price_days": result.no_price_days,
            "forward": _forward_rows(rates, result.start),
            "series": {
                "dates": [day.check_in.isoformat() for day in result.days if not result.horizon
                          or day.check_in <= result.horizon],
                "rates": [rates.get(day.check_in) for day in result.days if not result.horizon
                          or day.check_in <= result.horizon],
                "no_price": [day.check_in.isoformat() for day in result.days if day.status is DayStatus.NO_PRICE],
            },
        }

    # -- compsets (studies) -------------------------------------------------------------------------------------------

    @app.get("/api/studies")
    async def studies():
        with session_scope(factory) as session:
            rows = []
            for study in list_studies(session):
                runs = study_runs(session, study, limit=1)
                rows.append({
                    **_study_json(study),
                    "hotels": len(study.members),
                    "last_run": _run_json(runs[0], live_runs) if runs else None,
                })
            return {"studies": rows}

    @app.post("/api/studies", status_code=201)
    async def new_study(body: StudyIn):
        with session_scope(factory) as session:
            study = _guard(lambda: create_study(
                session, body.name, city=body.city, currency=body.currency, country=body.country,
                adults=body.adults, calendar_nights=body.calendar_nights, offers_months=body.offers_months,
            ))
            return _study_json(study)

    @app.get("/api/studies/{study_id}")
    async def study_detail(study_id: int):
        with session_scope(factory) as session:
            study = _guard(lambda: get_study(session, study_id), status=404)
            return {
                **_study_json(study),
                "hotels": [
                    {
                        "id": member.hotel.id, "name": member.hotel.name, "role": member.role,
                        "source_id": member.hotel.source_id, "hotel_class": member.hotel.star_class,
                        "rating": member.hotel.rating, "reviews_count": member.hotel.reviews_count,
                        "google_url": member.hotel.google_url,
                    }
                    for member in study_hotels(session, study)
                ],
                "events": [
                    {"id": event.id, "name": event.name, "event_date": event.event_date.isoformat(),
                     "end_date": event.end_date.isoformat() if event.end_date else None}
                    for event in study_events(session, study)
                ],
                "runs": [_run_json(run, live_runs) for run in study_runs(session, study, limit=10)],
            }

    @app.post("/api/studies/{study_id}/hotels", status_code=201)
    async def add_hotel(study_id: int, body: HotelIn):
        with session_scope(factory) as session:
            study = _guard(lambda: get_study(session, study_id), status=404)
            candidate = HotelCandidate(
                source_id=body.source_id, name=body.name, hotel_class=body.hotel_class, rating=body.rating,
                reviews_count=body.reviews_count, google_url=body.google_url,
            )
            hotel = upsert_hotel(session, source_id=body.source_id, name=body.name, city=study.city,
                                 country=study.country, candidate=candidate)
            _guard(lambda: add_hotel_to_study(session, study, hotel, role=body.role, input_name=body.name))
            return {"id": hotel.id, "name": hotel.name, "role": body.role}

    @app.delete("/api/studies/{study_id}/hotels/{hotel_id}", status_code=204)
    async def remove_hotel(study_id: int, hotel_id: int):
        with session_scope(factory) as session:
            study = _guard(lambda: get_study(session, study_id), status=404)
            _guard(lambda: remove_hotel_from_study(session, study, hotel_id), status=404)

    # -- runs ---------------------------------------------------------------------------------------------------------

    @app.post("/api/studies/{study_id}/runs", status_code=202)
    async def start_run(study_id: int, body: RunIn):
        if any(live.active and live.study_id == study_id for live in live_runs.values()):
            raise HTTPException(409, "A collection for this compset is already running")
        with session_scope(factory) as session:
            study = _guard(lambda: get_study(session, study_id), status=404)
            if not study.members:
                raise HTTPException(400, "Add hotels to the compset first")
        live = LiveRun(study_id=study_id)

        async def progress(message: str) -> None:
            live.messages.append(f"{datetime.now():%H:%M:%S} {message}")

        runner = StudyRunner(factory, google(), timezone_name=settings.store_timezone,
                             raw_mode=settings.store_raw_responses, on_progress=progress, today=today)
        run_id = runner.create_run(study_id, trigger="manual", location="web")
        live_runs[run_id] = live

        async def execute() -> None:
            try:
                summary = await runner.execute(run_id, calendar_only=body.calendar_only)
                await progress(f"finished: {summary.status}, {summary.requests_sent} requests")
            except asyncio.CancelledError:
                live.error = "stopped"
                raise
            except Exception as error:  # noqa: BLE001 - reported through the run status
                logger.exception("run %s failed", run_id)
                live.error = f"{type(error).__name__}: {error}"

        live.task = asyncio.create_task(execute())
        await progress("started" + (" (calendars only)" if body.calendar_only else ""))
        return {"run_id": run_id}

    @app.get("/api/runs/{run_id}")
    async def run_status(run_id: int):
        with session_scope(factory) as session:
            run = session.get(Run, run_id)
            if run is None:
                raise HTTPException(404, f"No run {run_id}")
            return _run_json(run, live_runs, messages=True)

    # -- dashboard and report -----------------------------------------------------------------------------------------

    @app.get("/api/studies/{study_id}/dashboard")
    async def dashboard(study_id: int, nights: int | None = None):
        with session_scope(factory) as session:
            study = _guard(lambda: get_study(session, study_id), status=404)
            data = load_report_data(session, study)
        nights = nights or min(data.calendar_nights or [1])
        return {
            "currency": data.currency,
            "nights": nights,
            "nights_options": data.calendar_nights,
            "as_of": _iso(forward_as_of(data, nights)),
            "forward": _table_json(forward_vs_compset(data, nights)),
            "forward_hotels": _table_json(forward_averages(data, nights)),
            "series": _series(data, nights),
            "report": [_table_json(table) for table in build_report(data).tables],
        }

    @app.get("/api/studies/{study_id}/report.xlsx")
    async def report_xlsx(study_id: int):
        with session_scope(factory) as session:
            study = _guard(lambda: get_study(session, study_id), status=404)
            data = load_report_data(session, study)
        handle = tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False)
        handle.close()
        path = write_report(build_report(data), Path(handle.name))
        filename = f"{_slug(data.study_name)}_{date.today().isoformat()}.xlsx"
        return FileResponse(path, filename=filename, background=BackgroundTask(path.unlink, missing_ok=True),
                            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")

    # -- events -------------------------------------------------------------------------------------------------------

    @app.post("/api/events", status_code=201)
    async def new_event(body: EventIn):
        with session_scope(factory) as session:
            event = _guard(lambda: add_event(session, city=body.city, event_date=body.event_date,
                                             end_date=body.end_date, name=body.name, country=body.country))
            return {"id": event.id, "name": event.name, "event_date": event.event_date.isoformat()}

    return app


# -- helpers -----------------------------------------------------------------------------------------------------------


def _forward_rows(rates: dict[date, float], start: date) -> list[dict[str, Any]]:
    rows = []
    for days in FORWARD_WINDOWS:
        average, count = window_average(rates, start, days)
        rows.append({"days": days, "average": average, "dates": count})
    return rows


def _series(data: ReportData, nights: int) -> dict[str, Any]:
    start = forward_as_of(data, nights)
    if start is None:
        return {"dates": []}
    hotel_rates = {hotel.id: (data.calendars.get((hotel.id, nights)).rates
                              if (hotel.id, nights) in data.calendars else {}) for hotel in data.hotels}
    last = max((day for rates in hotel_rates.values() for day in rates), default=start)
    days = window_days(start, (last - start).days + 1)
    medians = comp_median_by_date(data, nights)
    comp_ids = [hotel.id for hotel in data.comps]

    def spread(day: date, pick) -> float | None:
        values = [hotel_rates[hotel_id][day] for hotel_id in comp_ids if day in hotel_rates[hotel_id]]
        return pick(values) if values else None

    target = data.target
    return {
        "dates": [day.isoformat() for day in days],
        "target": {"name": target.name, "rates": [hotel_rates[target.id].get(day) for day in days]} if target else None,
        "comps": [{"name": hotel.name, "rates": [hotel_rates[hotel.id].get(day) for day in days]} for hotel in data.comps],
        "median": [medians.get(day) for day in days],
        "low": [spread(day, min) for day in days],
        "high": [spread(day, max) for day in days],
    }


def _table_json(table: Table) -> dict[str, Any]:
    return {
        "title": table.title,
        "columns": [{"name": column.name, "kind": column.kind} for column in table.columns],
        "rows": [[_iso(value) for value in row] for row in table.rows],
        "total": [_iso(value) for value in table.total] if table.total is not None else None,
    }


def _study_json(study) -> dict[str, Any]:
    return {
        "id": study.id, "name": study.name, "city": study.city, "country": study.country,
        "currency": study.currency, "adults": study.adults, "calendar_days": study.calendar_days,
        "calendar_nights": study.calendar_nights, "offers_nights": study.offers_nights,
        "offers_months": study.offers_months,
    }


def _run_json(run: Run, live_runs: dict[int, LiveRun], *, messages: bool = False) -> dict[str, Any]:
    live = live_runs.get(run.id)
    data: dict[str, Any] = {
        "id": run.id, "status": run.status, "started_at": _iso(run.started_at), "finished_at": _iso(run.finished_at),
        "requests_sent": run.requests_sent, "counts": run.counts or {}, "error": run.error,
        "active": bool(live and live.active),
    }
    if messages:
        data["messages"] = list(live.messages) if live else []
        if live and live.error:
            data["error"] = live.error
    return data


def _iso(value: Any) -> Any:
    return value.isoformat() if isinstance(value, (date, datetime)) else value


def _guard(action, *, status: int = 400):
    try:
        return action()
    except StudyError as error:
        raise HTTPException(status, str(error)) from error


def _source_error(error: CollectorError) -> HTTPException:
    status = 404 if isinstance(error, NotFound) else 502
    return HTTPException(status, f"Google Hotels: {type(error).__name__}: {error}")


def _slug(name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-") or "study"


"""Running a study: every hotel's price calendars, then channel offers on sample and event dates, all stored.

Every fetch first claims its key in the once-per-day ledger (docs/DESIGN.md, section 4). If the same key already
succeeded today, by this study or any other, the run links to those observations instead of calling Google again.
A failed key can be retried the same day; a key another process is fetching right now is skipped.

Each fetch commits on its own, so an interrupted run keeps everything it collected.
"""

from __future__ import annotations

import asyncio
import gzip
import json
import logging
from collections import Counter, defaultdict
from collections.abc import Awaitable, Callable
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from hotelrate.rateshop.errors import Blocked, CollectorError, NotFound, ParseError
from hotelrate.rateshop.google_hotels import GoogleHotelsCollector, RawResponse
from hotelrate.rateshop.models import CalendarResult, DayStatus, OffersResult
from hotelrate.rateshop.sampling import sample_dates
from hotelrate.store.db import session_scope
from hotelrate.store.models import Fetch, Hotel, RateObservation, Run, RunFetch, Study
from hotelrate.store.models import RawResponse as RawRow
from hotelrate.store.studies import event_days, study_events, study_hotels

logger = logging.getLogger(__name__)

# A pending claim older than this is treated as abandoned (the process that made it died).
STALE_CLAIM = timedelta(hours=1)
RAW_MODES = ("none", "failures", "all")

Progress = Callable[[str], Awaitable[None]]
_current_fetch: ContextVar[int | None] = ContextVar("current_fetch", default=None)


@dataclass
class RunSummary:
    run_id: int
    status: str
    counts: dict[str, dict[str, int]]
    requests_sent: int
    elapsed_seconds: float
    errors: list[str] = field(default_factory=list)


@dataclass
class _Claim:
    fetch_id: int
    action: str  # fetch | reuse | busy


def fetch_key(
    *,
    collector: str,
    source_id: str,
    task: str,
    check_in: date,
    nights: int,
    adults: int,
    currency: str,
    fetch_date: date,
    calendar_days: int | None = None,
) -> str:
    days = f"|{calendar_days}d" if calendar_days is not None else ""
    return (
        f"{collector}|{source_id}|{task}|{check_in.isoformat()}|{nights}n|{adults}a|{currency}{days}"
        f"|{fetch_date.isoformat()}"
    )


def local_today(timezone_name: str) -> date:
    return datetime.now(ZoneInfo(timezone_name)).date()


class StudyRunner:
    def __init__(
        self,
        factory: sessionmaker[Session],
        collector: GoogleHotelsCollector,
        *,
        timezone_name: str = "Europe/London",
        raw_mode: str = "failures",
        today: date | None = None,
        on_progress: Progress | None = None,
    ) -> None:
        if raw_mode not in RAW_MODES:
            raise ValueError(f"raw_mode must be one of {RAW_MODES}")
        self.factory = factory
        self.collector = collector
        self.raw_mode = raw_mode
        self.today = today or local_today(timezone_name)
        self.on_progress = on_progress
        self._raw: dict[int, list[RawResponse]] = defaultdict(list)
        self._counts: dict[str, Counter[str]] = {"calendar": Counter(), "offers": Counter()}
        self._errors: list[str] = []

    async def run(
        self, study_id: int, *, trigger: str = "manual", location: str | None = None, calendar_only: bool = False
    ) -> RunSummary:
        run_id = self.create_run(study_id, trigger=trigger, location=location)
        return await self.execute(run_id, calendar_only=calendar_only)

    def create_run(self, study_id: int, *, trigger: str = "manual", location: str | None = None) -> int:
        """Record a run as started and return its id; `execute` does the collecting."""
        with session_scope(self.factory) as session:
            if session.get(Study, study_id) is None:
                raise ValueError(f"No study {study_id}")
            run = Run(
                study_id=study_id,
                trigger=trigger,
                status="running",
                location=location,
                collector_version=self.collector.version,
                started_at=datetime.now(timezone.utc),
                requests_sent=0,
                counts={},
            )
            session.add(run)
            session.flush()
            return run.id

    async def execute(self, run_id: int, *, calendar_only: bool = False) -> RunSummary:
        requests_before = self.collector.requests_sent
        with session_scope(self.factory) as session:
            run = session.get(Run, run_id)
            started = _aware(run.started_at) if run.started_at else datetime.now(timezone.utc)
            study = session.get(Study, run.study_id)
            members = [(member.hotel, member.role) for member in study_hotels(session, study)]
            extra_dates = sorted({day for event in study_events(session, study) for day in event_days(event)})
            session.expunge_all()

        self.collector.add_raw_listener(self._keep_raw)
        try:
            if not members:
                self._errors.append("the study has no hotels")
            await asyncio.gather(
                *(self._run_hotel(run_id, study, hotel, extra_dates, calendar_only) for hotel, _role in members)
            )
        except BaseException as error:
            self._finish(run_id, "failed", self.collector.requests_sent - requests_before,
                         f"interrupted: {type(error).__name__}: {error}")
            raise
        finally:
            self.collector.remove_raw_listener(self._keep_raw)

        counts = {task: dict(counter) for task, counter in self._counts.items()}
        total = sum(sum(counter.values()) for counter in self._counts.values())
        good = sum(counter["ok"] + counter["reused"] for counter in self._counts.values())
        status = "succeeded" if total and good == total else "partial" if good else "failed"
        requests = self.collector.requests_sent - requests_before
        finished = self._finish(run_id, status, requests, "; ".join(self._errors[:20]) or None)
        return RunSummary(
            run_id=run_id,
            status=status,
            counts=counts,
            requests_sent=requests,
            elapsed_seconds=round((finished - started).total_seconds(), 1),
            errors=list(self._errors),
        )

    def _finish(self, run_id: int, status: str, requests: int, error: str | None) -> datetime:
        finished = datetime.now(timezone.utc)
        with session_scope(self.factory) as session:
            run = session.get(Run, run_id)
            run.status = status
            run.finished_at = finished
            run.requests_sent = requests
            run.counts = {task: dict(counter) for task, counter in self._counts.items()}
            run.error = error
        return finished

    # -- per hotel ----------------------------------------------------------------------------------------------------

    async def _run_hotel(
        self, run_id: int, study: Study, hotel: Hotel, extra_dates: list[date], calendar_only: bool
    ) -> None:
        horizon: date | None = None
        for nights in study.calendar_nights:
            found = await self._calendar(run_id, study, hotel, nights)
            if found is not None and (horizon is None or nights == min(study.calendar_nights)):
                horizon = found
        await self._progress(f"{hotel.name}: calendars done, horizon {horizon or 'unknown'}")
        if calendar_only:
            return
        last_day = horizon or self.today + timedelta(days=study.calendar_days - 1)
        dates = sample_dates(self.today, months=study.offers_months, horizon=last_day, extra=extra_dates)
        outcomes = await asyncio.gather(*(self._offers(run_id, study, hotel, day) for day in dates))
        tally = Counter(outcomes)
        await self._progress(f"{hotel.name}: offers on {len(dates)} dates: {dict(tally)}")

    async def _calendar(self, run_id: int, study: Study, hotel: Hotel, nights: int) -> date | None:
        key = fetch_key(
            collector=self.collector.name, source_id=hotel.source_id, task="calendar", check_in=self.today,
            nights=nights, adults=study.adults, currency=study.currency, fetch_date=self.today,
            calendar_days=study.calendar_days,
        )
        claim = self._claim(run_id, key, hotel, "calendar", self.today, nights, study, study.calendar_days)
        if claim.action != "fetch":
            self._counts["calendar"][_claim_count(claim.action)] += 1
            return self._stored_horizon(claim.fetch_id) if claim.action == "reuse" else None

        async def call() -> CalendarResult:
            return await self.collector.price_calendar(
                hotel.source_id, start=self.today, days=study.calendar_days, nights=nights, adults=study.adults,
                currency=study.currency,
            )

        result = await self._fetch(claim.fetch_id, "calendar", hotel, call)
        if result is None:
            return None
        rows = [
            RateObservation(
                hotel_id=hotel.id, fetch_id=claim.fetch_id, source_task="calendar", check_in=day.check_in,
                nights=nights, adults=study.adults, currency=study.currency,
                available=day.status is DayStatus.PRICED, day_status=day.status.value,
                price_per_night=day.rate_per_night, price_total=day.total,
                total_before_taxes=day.total_before_taxes, taxes=day.taxes_and_fees, observed_at=result.fetched_at,
            )
            for day in result.days
        ]
        self._record_ok(claim.fetch_id, rows)
        return result.horizon

    async def _offers(self, run_id: int, study: Study, hotel: Hotel, check_in: date) -> str:
        key = fetch_key(
            collector=self.collector.name, source_id=hotel.source_id, task="offers", check_in=check_in,
            nights=study.offers_nights, adults=study.adults, currency=study.currency, fetch_date=self.today,
        )
        claim = self._claim(run_id, key, hotel, "offers", check_in, study.offers_nights, study, None)
        if claim.action != "fetch":
            outcome = _claim_count(claim.action)
            self._counts["offers"][outcome] += 1
            return outcome

        async def call() -> OffersResult:
            return await self.collector.channel_offers(
                hotel.source_id, check_in=check_in, nights=study.offers_nights, adults=study.adults,
                currency=study.currency,
            )

        result = await self._fetch(claim.fetch_id, "offers", hotel, call)
        if result is None:
            return "failed"
        common = {
            "hotel_id": hotel.id, "fetch_id": claim.fetch_id, "source_task": "offers", "check_in": check_in,
            "nights": study.offers_nights, "adults": study.adults, "currency": study.currency,
            "observed_at": result.fetched_at,
        }
        rows = [
            RateObservation(
                **common, available=offer.price_per_night is not None, channel=offer.channel,
                is_official_site=offer.is_official_site, is_sponsored=offer.is_sponsored, room=offer.room,
                rate_plan=offer.rate_plan, free_cancellation=offer.free_cancellation,
                cancellation_deadline=offer.cancellation_deadline, breakfast=offer.breakfast,
                is_member_rate=offer.is_member_rate, price_per_night=offer.price_per_night,
                price_total=offer.price_total,
            )
            for offer in result.offers
        ]
        if not rows:
            # No channel showed a price: recorded, so "no offers" is not mistaken for "not collected".
            rows = [RateObservation(**common, available=False)]
        self._record_ok(claim.fetch_id, rows)
        if result.latitude is not None and hotel.latitude is None:
            self._place(hotel, result.latitude, result.longitude, result.address)
        return "ok"

    async def _fetch(self, fetch_id: int, task: str, hotel: Hotel, call):
        token = _current_fetch.set(fetch_id)
        try:
            return await call()
        except Blocked as error:
            self._record_failure(fetch_id, task, "blocked", error, hotel)
        except NotFound as error:
            self._record_failure(fetch_id, task, "not_found", error, hotel)
        except ParseError as error:
            self._record_failure(fetch_id, task, "parse_error", error, hotel)
        except CollectorError as error:
            self._record_failure(fetch_id, task, "failed", error, hotel)
        except Exception as error:  # noqa: BLE001 - one fetch never stops the run
            logger.exception("unexpected failure in %s fetch for %s", task, hotel.name)
            self._record_failure(fetch_id, task, "failed", error, hotel)
        finally:
            _current_fetch.reset(token)
        return None

    # -- ledger -------------------------------------------------------------------------------------------------------

    def _claim(
        self, run_id: int, key: str, hotel: Hotel, task: str, check_in: date, nights: int, study: Study,
        calendar_days: int | None,
    ) -> _Claim:
        now = datetime.now(timezone.utc)
        with session_scope(self.factory) as session:
            existing = session.scalar(select(Fetch).where(Fetch.fetch_key == key))
            if existing is None:
                fetch = Fetch(
                    fetch_key=key, collector=self.collector.name, collector_version=self.collector.version,
                    hotel_id=hotel.id, task=task, check_in=check_in, nights=nights, adults=study.adults,
                    currency=study.currency, calendar_days=calendar_days, fetch_date=self.today, run_id=run_id,
                    outcome="pending", attempts=1, requests=0, claimed_at=now,
                )
                session.add(fetch)
                try:
                    session.flush()
                except IntegrityError:
                    session.rollback()
                    return self._claim(run_id, key, hotel, task, check_in, nights, study, calendar_days)
                session.add(RunFetch(run_id=run_id, fetch_id=fetch.id, reused=False))
                return _Claim(fetch.id, "fetch")
            if existing.outcome == "ok":
                session.merge(RunFetch(run_id=run_id, fetch_id=existing.id, reused=True))
                return _Claim(existing.id, "reuse")
            claimed_at = _aware(existing.claimed_at)
            if existing.outcome == "pending" and existing.run_id != run_id and now - claimed_at < STALE_CLAIM:
                return _Claim(existing.id, "busy")
            existing.outcome = "pending"
            existing.error = None
            existing.attempts += 1
            existing.run_id = run_id
            existing.claimed_at = now
            session.merge(RunFetch(run_id=run_id, fetch_id=existing.id, reused=False))
            return _Claim(existing.id, "fetch")

    def _record_ok(self, fetch_id: int, rows: list[RateObservation]) -> None:
        raws = self._raw.pop(fetch_id, [])
        with session_scope(self.factory) as session:
            fetch = session.get(Fetch, fetch_id)
            session.add_all(rows)
            fetch.outcome = "ok"
            fetch.fetched_at = datetime.now(timezone.utc)
            fetch.requests += len(raws)
            if self.raw_mode == "all":
                session.add_all(self._raw_rows(fetch_id, raws))
            self._counts[fetch.task]["ok"] += 1

    def _record_failure(self, fetch_id: int, task: str, outcome: str, error: Exception, hotel: Hotel) -> None:
        raws = self._raw.pop(fetch_id, [])
        message = f"{type(error).__name__}: {error}"
        self._errors.append(f"{hotel.name} {task}: {message}")
        with session_scope(self.factory) as session:
            fetch = session.get(Fetch, fetch_id)
            fetch.outcome = outcome
            fetch.error = message[:2000]
            fetch.fetched_at = datetime.now(timezone.utc)
            fetch.requests += len(raws)
            if self.raw_mode != "none":
                session.add_all(self._raw_rows(fetch_id, raws))
                if isinstance(error, ParseError) and error.raw is not None and not raws:
                    session.add(RawRow(
                        fetch_id=fetch_id, collector_version=self.collector.version, rpc_id="unknown", request="",
                        problem=str(error), body_gzip=gzip.compress(error.raw.encode()),
                        captured_at=datetime.now(timezone.utc),
                    ))
        self._counts[task][outcome] += 1

    def _raw_rows(self, fetch_id: int, raws: list[RawResponse]) -> list[RawRow]:
        return [
            RawRow(
                fetch_id=fetch_id, collector_version=self.collector.version, rpc_id=raw.rpc_id,
                request=json.dumps(raw.request, separators=(",", ":")), http_status=raw.status, problem=raw.problem,
                body_gzip=gzip.compress(raw.body.encode()) if raw.body is not None else None, captured_at=raw.at,
            )
            for raw in raws
        ]

    def _place(self, hotel: Hotel, latitude: float, longitude: float | None, address: str | None) -> None:
        with session_scope(self.factory) as session:
            row = session.get(Hotel, hotel.id)
            if row is not None and row.latitude is None:
                row.latitude, row.longitude = latitude, longitude
                row.address = row.address or address
        hotel.latitude, hotel.longitude = latitude, longitude

    def _keep_raw(self, raw: RawResponse) -> None:
        fetch_id = _current_fetch.get()
        if fetch_id is not None:
            self._raw[fetch_id].append(raw)

    def _stored_horizon(self, fetch_id: int) -> date | None:
        with session_scope(self.factory) as session:
            return session.scalar(
                select(func.max(RateObservation.check_in)).where(
                    RateObservation.fetch_id == fetch_id, RateObservation.available.is_(True)
                )
            )

    async def _progress(self, message: str) -> None:
        if self.on_progress is not None:
            await self.on_progress(message)


def _claim_count(action: str) -> str:
    return {"reuse": "reused", "busy": "busy"}[action]


def _aware(moment: datetime) -> datetime:
    # SQLite hands timestamps back without a zone; they were written in UTC.
    return moment if moment.tzinfo else moment.replace(tzinfo=timezone.utc)

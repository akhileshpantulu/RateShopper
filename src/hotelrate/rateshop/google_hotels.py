"""Google Hotels collector: direct calls to Google Travel's data endpoints, no browser.

Built on the `google-hotels` library (pinned exactly, MIT). Its request builders, parsers and plans are used as they
are; its transport is replaced with ours (docs/DESIGN.md, finding F2), so every call follows `RequestPolicy`: our
retry cap, in-flight limit, pauses and block handling, instead of the library's 5 fixed retries. This reaches into
the library's internal modules; `tests/test_google_hotels_contract.py` fails the build if an upgrade changes them.

One call returns, for one hotel:
- search: up to 20 candidate hotels for a name (resolve_hotel),
- calendar: the price of the same stay for every check-in day, about 300 days ahead (price_calendar),
- prices: every booking channel's price for one stay (channel_offers).
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any

import google_hotels
import httpx
from google_hotels import plans
from google_hotels._protocol import constants
from google_hotels._protocol.batchexecute import encode_call, read_payload
from google_hotels.arguments import filters_of, reference_of
from google_hotels.errors import GoogleHotelsError, HotelNotFoundError, InvalidRequestError
from google_hotels.options import SearchFilters, Stay, make_stay
from google_hotels.runner import run_async

from hotelrate.config import Settings, get_settings
from hotelrate.core.browser_policy import http_user_agent
from hotelrate.rateshop.errors import Blocked, CollectorError, NotFound, ParseError, SourceError
from hotelrate.rateshop.models import (
    CalendarDay,
    CalendarResult,
    DayStatus,
    HotelCandidate,
    OfferRow,
    OffersResult,
)
from hotelrate.rateshop.policy import PolicyConfig, RequestPolicy

logger = logging.getLogger(__name__)

COLLECTOR_NAME = "google_hotels"
COLLECTOR_VERSION = f"google-hotels=={google_hotels.__version__}"
BLOCK_PAGE_PATH = "/sorry/"
BLOCK_STATUS_CODES = {429}
TRANSIENT_STATUS_CODES = {500, 502, 503, 504}
MAX_CALENDAR_DAYS = constants.MAX_CALENDAR_DAYS
MAX_CANDIDATES = 20


@dataclass
class RawResponse:
    """One answer exactly as received, for parser-failure records and test fixtures."""

    rpc_id: str
    request: list[Any]
    status: int | None
    url: str | None
    body: str | None
    problem: str | None
    at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def to_fixture(self) -> dict[str, Any]:
        return {"rpc_id": self.rpc_id, "request": self.request, "status": self.status, "url": self.url, "body": self.body}


@dataclass
class _Attempt:
    kind: str  # ok | blocked | transient | parse | fatal
    payload: Any = None
    problem: str | None = None
    body: str | None = None


class PolicyTransport:
    """Sends one Google Travel call under the request policy. Matches the library runner's transport interface."""

    def __init__(
        self,
        settings: Settings,
        policy: RequestPolicy,
        *,
        http_transport: httpx.AsyncBaseTransport | None = None,
        on_raw: Callable[[RawResponse], None] | None = None,
    ) -> None:
        self.language = settings.google_hotels_language.lower()
        self.country = settings.google_hotels_country.lower()
        self.policy = policy
        self.on_raw = on_raw
        self.last_body: str | None = None
        self.user_agent = http_user_agent(settings, constants.REQUEST_HEADERS["User-Agent"])
        self._http = httpx.AsyncClient(
            headers={
                "User-Agent": self.user_agent,
                "Accept": "*/*",
                "Accept-Language": f"{self.language},en;q=0.8",
            },
            timeout=httpx.Timeout(constants.REQUEST_TIMEOUT_SECONDS, read=constants.ATTEMPT_DEADLINE_SECONDS),
            follow_redirects=True,
            transport=http_transport,
        )

    async def aclose(self) -> None:
        await self._http.aclose()

    async def call(self, rpc_id: str, request: list[Any]) -> Any:
        last = _Attempt("transient", problem="no attempt made")
        for attempt in range(1 + self.policy.max_retries):
            if attempt:
                await self.policy.wait(self.policy.retry_delay(attempt))
            async with self.policy.slot():
                last = await self._attempt(rpc_id, request)
            if last.kind == "ok":
                return last.payload
            if last.kind == "fatal":
                raise SourceError(f"{rpc_id}: {last.problem}")
            if last.kind == "blocked":
                self.policy.note_block()
            logger.warning("Google %s attempt %d failed: %s", rpc_id, attempt + 1, last.problem)
        tries = 1 + self.policy.max_retries
        message = f"{rpc_id} failed after {tries} attempt(s): {last.problem}"
        if last.kind == "blocked":
            raise Blocked(message)
        if last.kind == "parse":
            raise ParseError(message, raw=last.body)
        raise SourceError(message)

    async def _attempt(self, rpc_id: str, request: list[Any]) -> _Attempt:
        params = {"rpcids": rpc_id, "source-path": constants.RPC_SOURCE_PATH, "hl": self.language,
                  "gl": self.country, "rt": "c"}
        response: httpx.Response | None = None
        try:
            # A throttled connection can trickle bytes past the read timeout, so each attempt gets a total deadline.
            async with asyncio.timeout(constants.ATTEMPT_DEADLINE_SECONDS):
                response = await self._http.post(
                    constants.BATCHEXECUTE_URL, params=params, data={"f.req": encode_call(rpc_id, request)}
                )
            outcome = self._classify(response, rpc_id)
        except TimeoutError:
            outcome = _Attempt("transient", problem=f"no complete answer within {constants.ATTEMPT_DEADLINE_SECONDS}s")
        except httpx.HTTPError as error:
            outcome = _Attempt("transient", problem=f"network error {type(error).__name__}: {error}")
        self.last_body = outcome.body
        if self.on_raw is not None:
            self.on_raw(
                RawResponse(
                    rpc_id=rpc_id,
                    request=request,
                    status=response.status_code if response is not None else None,
                    url=str(response.url) if response is not None else None,
                    body=outcome.body,
                    problem=outcome.problem,
                )
            )
        return outcome

    @staticmethod
    def _classify(response: httpx.Response, rpc_id: str) -> _Attempt:
        body = response.text
        if BLOCK_PAGE_PATH in str(response.url):
            return _Attempt("blocked", problem="Google showed its unusual-traffic page", body=body)
        if response.status_code in BLOCK_STATUS_CODES:
            return _Attempt("blocked", problem=f"HTTP {response.status_code}", body=body)
        if response.status_code in TRANSIENT_STATUS_CODES:
            return _Attempt("transient", problem=f"HTTP {response.status_code}", body=body)
        if response.status_code != 200:
            return _Attempt("fatal", problem=f"unexpected HTTP {response.status_code}", body=body)
        try:
            payload, problem = read_payload(body, rpc_id)
        except InvalidRequestError as error:
            return _Attempt("fatal", problem=str(error), body=body)
        if problem is not None:
            # Google also answers a throttled call this way, so it is retried before it counts as a parse failure.
            return _Attempt("parse", problem=problem, body=body)
        return _Attempt("ok", payload=payload, body=body)


def _today_utc() -> date:
    return datetime.now(timezone.utc).date()


class GoogleHotelsCollector:
    name = COLLECTOR_NAME
    version = COLLECTOR_VERSION

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        policy: RequestPolicy | None = None,
        http_transport: httpx.AsyncBaseTransport | None = None,
        on_raw: Callable[[RawResponse], None] | None = None,
        today: Callable[[], date] = _today_utc,
    ) -> None:
        self.settings = settings or get_settings()
        self.policy = policy or RequestPolicy(PolicyConfig.from_settings(self.settings))
        self.locale = plans.Locale(self.settings.google_hotels_language.lower(), self.settings.google_hotels_country.lower())
        self._today = today
        self._transport = PolicyTransport(self.settings, self.policy, http_transport=http_transport, on_raw=on_raw)

    async def __aenter__(self) -> GoogleHotelsCollector:
        return self

    async def __aexit__(self, *_exc: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        await self._transport.aclose()

    @property
    def requests_sent(self) -> int:
        return self.policy.requests_started

    # -- public calls -------------------------------------------------------------------------------------------------

    async def resolve_hotel(
        self, name: str, city: str = "", country: str = "", *, max_candidates: int = 5
    ) -> list[HotelCandidate]:
        """Up to `max_candidates` hotels for a name, in Google's order, for a person to pick the right one."""
        query = " ".join(part.strip() for part in (name, city, country) if part and part.strip())
        if not query:
            raise ValueError("A hotel name is required")
        count = max(1, min(max_candidates, MAX_CANDIDATES))
        stay = self._stay(None, 1, 2, self.settings.default_currency)
        records = await self._run(plans.search(query, stay, filters_of(), self.locale, count, False))
        return [
            HotelCandidate(
                source_id=record["propertyToken"],
                name=record.get("name"),
                hotel_class=record.get("hotelClass"),
                property_type=record.get("propertyType"),
                rating=record.get("overallRating"),
                reviews_count=record.get("reviewsCount"),
                latitude=record.get("latitude"),
                longitude=record.get("longitude"),
                rate_per_night=record.get("ratePerNight"),
                currency=record.get("currency"),
                google_url=record.get("googleHotelsUrl"),
            )
            for record in records
            if record.get("propertyToken")
        ]

    async def price_calendar(
        self,
        hotel: str,
        *,
        start: date | None = None,
        days: int = 365,
        nights: int = 1,
        adults: int = 2,
        currency: str | None = None,
    ) -> CalendarResult:
        """The price of a stay of `nights` for every check-in day from `start`, with no-price days made explicit.

        `hotel` is a property token, a Google Hotels link, or a name with the city. A name is accepted only when
        Google's first result matches it; otherwise NotFound is raised rather than returning another hotel's prices.
        """
        if not 1 <= days <= MAX_CALENDAR_DAYS:
            raise ValueError(f"days must be from 1 to {MAX_CALENDAR_DAYS}, got {days}")
        code = (currency or self.settings.default_currency).upper()
        # Without a start the library would begin 14 days out, as Google's page does; a rate shop starts today.
        stay = self._stay(start or self._today(), nights, adults, code)
        reference = reference_of(hotel)

        def plan() -> plans.Plan[tuple[str, str, list[dict[str, Any]]]]:
            token, matched_by = yield from plans._resolve(reference, stay, SearchFilters())
            priced = yield from plans.price_calendar(token, stay, days)
            return token, matched_by, priced

        token, matched_by, priced = await self._run(plan())
        by_date = {row["checkInDate"]: row for row in priced}
        horizon = max((date.fromisoformat(key) for key in by_date), default=None)
        rows: list[CalendarDay] = []
        for offset in range(days):
            check_in = stay.check_in + timedelta(days=offset)
            found = by_date.get(check_in.isoformat())
            if found is not None:
                rows.append(
                    CalendarDay(
                        check_in=check_in,
                        check_out=check_in + timedelta(days=nights),
                        status=DayStatus.PRICED,
                        rate_per_night=found.get("ratePerNight"),
                        total=found.get("totalRate"),
                        total_before_taxes=found.get("totalRateBeforeTaxesAndFees"),
                        taxes_and_fees=found.get("taxesAndFees"),
                        currency=found.get("currency") or code,
                        cheapest=bool(found.get("isCheapestDate")),
                    )
                )
            else:
                in_horizon = horizon is not None and check_in <= horizon
                rows.append(
                    CalendarDay(
                        check_in=check_in,
                        check_out=check_in + timedelta(days=nights),
                        status=DayStatus.NO_PRICE if in_horizon else DayStatus.NOT_OFFERED,
                        currency=code,
                    )
                )
        return CalendarResult.build(
            hotel_id=token,
            matched_by=matched_by,
            start=stay.check_in,
            days=rows,
            nights=nights,
            adults=adults,
            currency=code,
            collector_version=self.version,
        )

    async def channel_offers(
        self,
        hotel: str,
        *,
        check_in: date,
        nights: int = 1,
        adults: int = 2,
        currency: str | None = None,
    ) -> OffersResult:
        """Every booking channel's price for one stay: booking sites and the hotel's own site."""
        code = (currency or self.settings.default_currency).upper()
        stay = self._stay(check_in, nights, adults, code)
        record = await self._run(plans.hotel(reference_of(hotel), stay, filters_of(), self.locale))
        rows = [row for source in record.get("bookingSources") or [] for row in _offer_rows(source, code)]
        prices = [row.price_per_night for row in rows if row.price_per_night is not None]
        lowest = record.get("lowestOfferRatePerNight")
        return OffersResult(
            hotel_id=record["propertyToken"],
            hotel_name=record.get("name"),
            matched_by=record.get("matchedBy") or "token",
            check_in=stay.check_in,
            check_out=stay.check_out,
            nights=stay.nights,
            adults=stay.adults,
            currency=code,
            lowest_price_per_night=lowest if lowest is not None else (min(prices) if prices else None),
            channels=len({row.channel for row in rows}),
            offers=rows,
            google_url=record.get("googleHotelsUrl"),
            collector_version=self.version,
        )

    # -- internals ----------------------------------------------------------------------------------------------------

    def _stay(self, check_in: date | None, nights: int, adults: int, currency: str) -> Stay:
        return make_stay(check_in, None, nights, adults, (), currency, today=self._today())

    async def _run(self, plan: plans.Plan[Any]) -> Any:
        try:
            return await run_async(plan, self._transport)
        except CollectorError:
            raise
        except HotelNotFoundError as error:
            raise NotFound(str(error)) from error
        except GoogleHotelsError as error:
            raise SourceError(str(error)) from error
        except (IndexError, KeyError, TypeError, ValueError, AttributeError) as error:
            raise ParseError(f"could not read Google's answer: {error!r}", raw=self._transport.last_body) from error


def derive_rate_plan(free_cancellation: bool, breakfast: bool) -> str:
    """A rate plan name from the two flags Google shows (finding F4). False means Google did not show the flag."""
    cancellation = "Free cancellation" if free_cancellation else "Standard cancellation"
    board = "breakfast" if breakfast else "room only"
    return f"{cancellation}, {board}"


def _offer_rows(source: dict[str, Any], currency: str) -> list[OfferRow]:
    base = {
        "channel": source.get("source") or "unknown",
        "is_official_site": bool(source.get("isOfficialSite")),
        "is_sponsored": bool(source.get("isSponsored")),
        "currency": currency,
        "partner_url": source.get("partnerUrl"),
    }
    is_member = source.get("nonMemberRatePerNight") is not None
    rows: list[OfferRow] = []
    for room in source.get("rooms") or []:
        for rate in room.get("rates") or []:
            free_cancellation = bool(rate.get("freeCancellation"))
            breakfast = bool(rate.get("breakfastIncluded"))
            rows.append(
                OfferRow(
                    **base,
                    room=room.get("name"),
                    rate_plan=derive_rate_plan(free_cancellation, breakfast),
                    free_cancellation=free_cancellation,
                    cancellation_deadline=rate.get("freeCancellationUntil"),
                    breakfast=breakfast,
                    price_per_night=rate.get("ratePerNight"),
                    price_total=rate.get("totalRate"),
                )
            )
    if not rows:
        rows.append(
            OfferRow(
                **base,
                free_cancellation=source.get("freeCancellationAvailable"),
                price_per_night=source.get("ratePerNight"),
                price_total=source.get("totalRate"),
                is_member_rate=is_member,
                non_member_price_per_night=source.get("nonMemberRatePerNight"),
            )
        )
    return rows


def raw_to_json_line(raw: RawResponse) -> str:
    return json.dumps(raw.to_fixture(), separators=(",", ":"))

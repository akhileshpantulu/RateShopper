# Compset Rate Shopper: Design and Phased Plan

Status: draft for approval. Revised on 2026-11-10: the project now builds on the imported `hotelrate-crawl` code
(see [section 12](#12-revision-building-on-hotelrate-crawl)). Sections 2 to 11 are the original plan. Where
section 12 differs, section 12 applies.

## 1. Purpose

An internal tool for about 10 people on the investment team. A user names a target hotel and 4 to 10 competitors,
the app collects the rates those hotels publicly list on Google Hotels, keeps every snapshot, and produces a
competitive pricing report on screen and as a one-tab Excel file. Only public data goes in: hotel names, cities,
event names and dates. No confidential figures are entered or stored.

## 2. Findings from the source library that affect the spec

I read the source of `google-hotels` 0.1.0 (the only published version, MIT, released October 2026). Five points
need a decision or change the plan:

| # | Finding | Effect | Proposal |
|---|---|---|---|
| F1 | Google prices about 300 check-in days ahead. A 400-day request returned 301 priced days. | The 365-day calendar cannot be met from this source. | Request 365 days; store what comes back; record the horizon reached per hotel and show it on the report. Dates beyond the horizon are "not offered", never counted as sell-outs. |
| F2 | The library retries a blocked call up to 5 times internally (fixed constant), and its default concurrency is 5. | Conflicts with "max 3 retries" and "max 2 concurrent". | Our adapter supplies its own transport to the library's request runner. Our transport enforces 1 attempt + up to 3 retries, a process-wide limit of 2 calls in flight, random delays, and captures every raw response. This uses the library's internal modules (`plans`, `runner`, parsers), which is acceptable only because the version is pinned exactly; a contract test fails the build if an upgrade changes them. |
| F3 | The price calendar parser silently drops dates with no price. | Sell-out detection needs to know which dates were missing. | We compare requested dates to returned dates inside the horizon and write an explicit "no price" observation for each gap. |
| F4 | Offers carry room name, per-night and total rate, free cancellation (with deadline) and breakfast flag. There is no named rate plan, and taxes are split out only in the price calendar. | "Rate plan" and "taxes" columns cannot always be filled from offers. | Rate plan is derived from the flags, e.g. "Free cancellation, breakfast". Taxes are stored where the source gives them and left empty otherwise. I will confirm from recorded responses whether offer totals include taxes, and label the report accordingly. |
| F5 | With a known property token, one price calendar is 1 call and one date's channel offers is 1 call. | Lets us size a run. | See section 6. |

## 3. Architecture

```
                      Microsoft Entra ID (SSO)
                               |
 Browser (React) --HTTPS--> Web container (FastAPI + built React)      Azure Container Apps, UK South
                               |
                               v
                  Azure Database for PostgreSQL (Flexible Server)
                   tables + job queue (SELECT ... FOR UPDATE SKIP LOCKED)
                       ^                                ^
                       |                                |
      Collector worker (Container App)        Collector worker (office machine, Docker)
      COLLECTOR_MODE=cloud                    COLLECTOR_MODE=office, same image
                       |                                |
                       +-----------> Google Hotels <----+     (only one location active at a time)

  Container Apps Jobs (cron): scheduler (hourly), canary (daily), alert check
  Key Vault: DB credentials, email connection string, Entra settings
  Azure Communication Services Email: alerts
  Log Analytics: container logs
```

Decisions and reasons:

- **Hosting: Azure Container Apps.** The web app and worker are the same Python package in two images (web image adds
  the React build). Container Apps Jobs give cron for the scheduler and canary without a separate service.
- **Sign-in: Container Apps built-in authentication with Entra ID.** The app registration requires user assignment,
  so only a named security group can sign in. FastAPI reads the signed-in user from the platform headers and rejects
  requests without them. Locally, a dev-only stub user is used.
- **Queue: PostgreSQL itself.** A `jobs` table polled with `FOR UPDATE SKIP LOCKED`. At this volume (a few thousand
  jobs a day at most) a separate broker adds cost and a second credential with no benefit, and job state, progress
  and the health page come from the same table with ordinary SQL. The worker talks only to the database.
- **Office worker.** Same image, run with Docker on an office PC. It connects to PostgreSQL over TLS using an Entra
  token or a dedicated low-privilege role, with the office static IP on the server firewall. A worker registers with
  a `location` label; a study or the global setting picks which location collects, so cloud and office workers never
  both hit Google for the same job. Google is generally stricter with cloud data-centre IP ranges than with office
  ranges, so I expect the office worker to be the main collector and the cloud worker the fallback. The canary will
  tell us within the first week.
- **Infrastructure as code: Bicep** (Azure only, no state file to manage). Terraform works equally well if the team
  already uses it.
- **CI/CD: GitHub Actions.** On pull request: lint, type check, unit tests, tests against a PostgreSQL service
  container, frontend tests. On merge to main: build images, push to Azure Container Registry, deploy with Bicep
  using OpenID Connect federation to Azure (no stored cloud passwords in GitHub).
- **Secrets.** Key Vault, read by container managed identities. The repository holds only `.env.example` with
  placeholder values.

## 4. Collector interface

All code outside `collectors/google_hotels/` depends only on this interface and its data classes:

```python
class RateCollector(Protocol):
    name: str            # "google_hotels"
    version: str         # "google-hotels==0.1.0"

    def resolve_hotel(self, name: str, city: str, country: str) -> list[HotelCandidate]: ...
    def price_calendar(self, hotel: HotelRef, start: date, days: int, nights: int, adults: int,
                       currency: str) -> CalendarResult: ...
    def channel_offers(self, hotel: HotelRef, check_in: date, nights: int, adults: int,
                       currency: str) -> OffersResult: ...
```

- `HotelCandidate`: source id (property token), name, address, star class, rating, latitude and longitude, a link
  to the Google Hotels page. Up to 5 candidates so the user can pick the right one.
- `CalendarResult`: one row per requested date (priced or "no price"), the horizon reached, raw response reference.
- `OffersResult`: one row per channel and room and rate (channel, official-site flag, room, rate plan, cancellation
  terms, breakfast, price per night, total, currency, taxes when known), raw response reference.
- Errors are typed: `Blocked`, `NotFound`, `ParseError` (carries the raw payload), `SourceError`. The job runner maps
  these to job outcomes; it never sees library exceptions.

The collection rules live in one shared component used by every collector, so a replacement collector inherits them:

- **Rate limiter:** at most 2 requests in flight per worker process; a random pause of 3 to 8 seconds between
  requests (configurable). Single IP, no proxy, no CAPTCHA handling, a fixed honest user agent from the library.
  The settings module rejects any proxy configuration.
- **Retries:** at most 3 retries per request with growing, jittered back-off (e.g. 30 s, 90 s, 270 s). On a block
  signal (HTTP 429, Google's unusual-traffic page) the whole worker pauses before the next request, not only that
  item. After the last retry the item is marked `blocked` and the run continues.
- **Once per day:** a `fetch_ledger` table with a unique key (collector, hotel, task, check-in date, nights, adults,
  currency, London calendar date). A job claims its key before calling the source. If the key is already taken
  today, by this study or any other, the job links to the existing observations instead of fetching. This also makes
  two studies sharing a hotel cost one fetch.

## 5. Data model

Observations are append-only: no `UPDATE` or `DELETE` on `rate_observations`, enforced by revoking those
privileges from the application role.

| Table | Key columns |
|---|---|
| `hotels` | id, collector, source_id (property token), name, address, city, country, star class, lat/lon, created_at |
| `studies` | id, name, created_by (Entra user), currency, adults (2), schedule (none/daily/weekly), schedule_end_date, collector_location, status |
| `study_hotels` | study_id, hotel_id, role (target/comp), input_name, confirmed_by, confirmed_at |
| `runs` | id, study_id, trigger (manual/schedule), status, requested_at, started_at, finished_at, counts by outcome |
| `jobs` | id, run_id, hotel_id, task (calendar_1n, calendar_2n, offers), params, status (queued/running/succeeded/blocked/failed/reused), attempts, locked_by, locked_at, error |
| `rate_observations` | id, hotel_id, job_id, fetch_id, check_in, nights, adults, channel, is_official_site, room, rate_plan, free_cancellation, cancellation_deadline, breakfast, price_per_night, price_total, currency, taxes, available (false = no price shown), source_task, observed_at |
| `events` | id, city, country, event_date, end_date, name, created_by |
| `job_log` | id, job_id, at, level, message (every attempt, wait and outcome) |
| `fetch_ledger` | unique fetch key (section 4), fetch_id, job_id, outcome |
| `raw_responses` | id, fetch_id, collector_version, request summary, compressed body, parse error. Kept for every parser failure and canary run, and for a configurable sample of successes (needed to refresh test fixtures). |
| `canary_results`, `alerts_sent` | for the health page and to avoid repeat emails |

`fetch_ledger`, `raw_responses`, `canary_results` and `alerts_sent` are additions to the tables you listed. A report
always reads observations by hotel and observation date, so history accumulates across runs and studies.

## 6. What a run does, and how long it takes

Per hotel, per run:

- `calendar_1n` and `calendar_2n`: 2 calls covering up to 365 days (about 300 in practice).
- `offers`: one call per sample date. Sample = one full week (Monday to Sunday) per month for 12 months, starting the
  second Monday of each month, plus every event date in the hotel's city: about 84 + events.

For 11 hotels with 10 event dates: 11 x (2 + 94) = about 1,060 calls if offers are collected for 1-night stays only,
about 2,100 if for both 1 and 2 nights. At 2 in flight with 3 to 8 second pauses that is roughly 1 hour or 2 hours.

Each hotel and task is a separate job, so a block or failure on one never stops the run. Offers jobs are
one job per hotel covering all its sample dates; each date inside the job has its own outcome, so the health page
counts dates, not just jobs.

## 7. Report

Built from observations as plain functions (no UI code), so the screen and the Excel export share one engine.
Every figure carries the observation date (or range) and the number of data points behind it.

1. **Rate position:** target vs compset median, min and max, by month and by day of week; target premium or discount
   to median in %. Source: latest calendar snapshot, 1-night and 2-night shown separately.
2. **Event response:** for each event date, each hotel's price vs its own average on the same weekday in the 2 weeks
   either side (excluding other event dates). Target uplift vs each comp's uplift.
3. **Parity:** hotel's own site vs each booking site, matched on date, nights and lowest comparable rate (same
   cancellation and breakfast terms where possible). Difference in currency and %, and share of dates where the
   own site is cheapest. For target and each comp.
4. **Room ladder:** for each hotel, the lowest price per room name, ordered, with the step between consecutive rooms
   in currency and %. Room names differ by hotel, so the ladder is shown as position 1, 2, 3 with the actual room
   name alongside.
5. **Sell-out signals:** dates inside the horizon where a hotel showed no price, per hotel, with a compset count per
   date (several hotels dark on one date is a stronger signal).
6. **Change since last run:** once 2 or more snapshots exist, change in each hotel's price by check-in month and for
   the next 30, 60 and 90 days, plus newly dark and newly priced dates.

Excel: one worksheet, sections stacked vertically with a heading row each, a header block stating study, hotels,
currency, snapshot dates and collector version. Built with openpyxl.

## 8. Reliability and monitoring

- **Health page:** per run, requested / succeeded / blocked / failed / reused, by task and by hotel, plus the latest
  canary results and worker heartbeats (which locations are online).
- **Canary:** a daily job resolves one fixed, well-known hotel, fetches a 30-day calendar and one date's offers, and
  checks the response shape (required fields present and typed, at least N priced days, at least one channel). The
  raw response is kept. A canary job runs from the same location as the active collector.
- **Alerts:** email via Azure Communication Services to the configured address when the canary fails, or when a
  finished run's success rate is below the threshold (default 80%). One email per incident.
- **Parser failures:** the raw response, collector version and error are stored in `raw_responses` and linked from
  the job on the health page.

## 9. Repository layout

```
backend/
  rateshopper/
    collectors/base.py          interface, data classes, typed errors
    collectors/policy.py        rate limiter, retry and back-off, raw capture
    collectors/google_hotels/   the only code that imports google_hotels
    db/                         SQLAlchemy models, Alembic migrations
    jobs/                       planning a run, queue, job runner, worker loop
    studies/                    study creation, CSV import, matching and confirmation
    reports/                    report engine and Excel export
    api/                        FastAPI (phase 4)
    cli.py
  tests/
    fixtures/google_hotels/     recorded responses
frontend/                       React + Vite (phase 4)
infra/                          Bicep (phase 5)
.github/workflows/
docs/
```

Stack: Python 3.12, FastAPI, SQLAlchemy 2, Alembic, psycopg 3, httpx, Typer for the CLI, pytest, ruff, mypy. React
with TypeScript and Vite. Dependencies pinned with a lock file; `google-hotels==0.1.0` pinned exactly.

## 10. Phased plan

Each phase ends with a demo and your sign-off before the next starts.

**Phase 1: collector, database, command-line run (this is the next step after approval)**
- Project skeleton, lint, type check, tests in GitHub Actions against a PostgreSQL service container.
- `RateCollector` interface, typed results and errors, the shared policy (limiter, retries, block handling,
  once-per-day ledger, raw capture).
- Google Hotels adapter with its own transport (finding F2), gap filling for no-price dates (F3), derived rate plan (F4).
- Schema and migrations for all tables in section 5, with append-only privileges.
- Job planning (calendar and sample dates incl. events) and a job runner that the phase 2 worker will reuse unchanged.
- CLI: `study create` (names or CSV), `study resolve` (shows candidates), `study confirm`, `events import` (CSV),
  `run` (executes the run's jobs in-process with the same limits), `run status`.
- Tests with recorded responses: a `record` command captures real responses into `tests/fixtures/`; a replay
  transport serves them in tests. Covers parsing, no-price gaps, blocks and the 3-retry cap, the 2-in-flight cap,
  the once-per-day rule, parser-failure capture, CSV validation, and a full CLI run against PostgreSQL.
- Recording needs network access to Google. If this cloud environment cannot reach it, I will hand you a one-line
  command to run from an office machine and commit the result.

**Phase 2: worker, scheduler, monitoring**
- Worker container polling the queue, heartbeats, `location` routing, graceful shutdown, stale-lock recovery.
- Scheduler job (daily or weekly until end date), canary job, alert job and email.
- Health data in the database and a `health` CLI view.

**Phase 3: report engine and Excel**
- The six report sections as tested functions over observations, with observation dates and counts on every figure.
- One-tab Excel export. CLI `report` command.

**Phase 4: web app**
- FastAPI endpoints and React UI: create study (form or CSV), confirm matches, events, run and schedule, live
  progress (server-sent events), report view and Excel download, health page.
- Entra sign-in through Container Apps authentication; local dev stub.

**Phase 5: Azure and delivery**
- Bicep for Container Apps environment, web app, worker, jobs, PostgreSQL Flexible Server, Key Vault, Container
  Registry, Log Analytics, Communication Services Email, all in UK South.
- GitHub Actions deploy with OpenID Connect. Office worker install guide and Docker Compose file.

## 11. Decisions I need from you

1. **Calendar horizon (F1):** accept "365 requested, about 300 delivered, horizon shown on the report"?
2. **Channel offers nights:** 1-night only (about 1 hour per run for 11 hotels) or 1 and 2 nights (about 2 hours)?
   I recommend 1-night only.
3. **Sample week:** Monday to Sunday starting the second Monday of each month. Acceptable, or a different rule?
4. **Library internals (F2):** acceptable to run the library's request runner with our own transport, given the exact
   version pin and a contract test? The alternative is to fork the library.
5. **Queue in PostgreSQL** with the office worker connecting to the database directly through a firewall rule for the
   office IP. Acceptable, or must the database stay private (then the worker would talk to an internal API instead)?
6. **Bicep or Terraform.** I recommend Bicep.
7. **Alert email address**, and confirm the 80% threshold.
8. **Default currency and locale:** GBP, English, UK? Can a study override currency?
9. **Daily schedules:** a daily run of a full study is about 1,000 requests a day from one IP. Fine to allow daily, or
   limit daily schedules to the price calendar and keep offers weekly?
10. **Canary hotel:** any preference? Otherwise I will pick a large, always-open London hotel.

## 12. Revision: building on hotelrate-crawl

### 12.1 What is in the repository now

`hotelrate-crawl` 0.3.0 (MIT) was imported as the code base. Origin and commit are recorded in
[UPSTREAM.md](../UPSTREAM.md). It provides, working today:

- Four site collectors (Booking.com, Agoda, Trip.com, Google Hotels) that load a hotel page in Chromium through
  Playwright and return room offers for one hotel and one stay.
- URL discovery that turns a hotel name and city into candidate page URLs, and a JSON cache of the URL that worked.
- An MCP server (`hotelrate-mcp`) with four tools: `hotel_quote`, `hotel_quote_batch`, `list_platforms`,
  `health_check`. `.mcp.json` registers it for Claude Code.
- A demo mode (the default) that returns synthetic prices without a browser.
- Starting points for later phases: a FastAPI app, an interval worker, a four-table PostgreSQL schema, and
  `setup` / `doctor` commands.

### 12.2 How it maps to this design

| Design item | Upstream code | Gap to close |
|---|---|---|
| Collector interface (section 4) | `collectors/base.py`: `fetch_rates(SearchRequest) -> list[RawRateOffer]` | One stay per call. No candidate list for the user to confirm, no price calendar, no typed errors (a failure is an empty list or a `RuntimeError`). |
| Hotel matching and confirmation | `services/url_discovery.py` picks a URL by token overlap; `url_mapping_cache.py` remembers it | Nothing confirms the page is the right hotel. Replace automatic choice with the confirm step; `known_urls` already lets a caller pin the URL. |
| Rate limiter and retries (section 4) | Semaphores (2 in flight per site), 250 ms stagger, one repeat after 1.1 s on an empty result, up to 8 attempts per site | No 3 to 8 second pauses, no 3-retry cap with back-off, no worker-wide pause on a block, no once-per-day ledger. |
| Data model (section 5) | `sql/schema.sql`, `infra/models.py`: `hotel_master`, `hotel_mapping`, `rate_snapshot`, `crawl_job_log` | No studies, runs, jobs, events, fetch ledger or raw responses. Snapshots are appended but nothing enforces append-only. |
| Queue and worker (sections 3, 6) | `apps/worker`: in-process scheduler that crawls every mapped hotel for one fixed stay on an interval | No queue, no per-job outcome, no worker location. |
| Report and Excel (section 7) | None | All of it. |
| Web app and sign-in (section 3) | `apps/api`: quote and rate-history endpoints | No UI, no authentication. |
| Health, canary, alerts (section 8) | `hotelrate-mcp doctor`, `health_check` tool | No canary, no alerts, no health page. |
| MCP access | `apps/mcp` | Not in the original design. Kept as an extra way to ask for a quote. |

### 12.3 Effect on run size

The original plan gets a hotel's whole price calendar (about 300 dates) in one call. The imported collectors price
one hotel, one site and one stay per page load. For 11 hotels:

| Task | Original plan | Imported collectors |
|---|---|---|
| 1-night price calendar, about 300 dates | 11 calls | 3,300 page loads per site |
| Offers on about 94 sample dates | about 1,030 calls | about 1,030 page loads per site |

So the imported collectors fit the `channel_offers` task and add a direct view of each booking site, which the
original plan did not have. They cannot replace the price calendar. Proposal: keep the `google-hotels` library
(section 2) as the calendar source, and use the imported collectors for offers on sample and event dates.

### 12.4 Collection rules

Changed at import, so the defaults match section 4:

- **No masking by default.** Upstream hid the browser's automation flag, rotated fake desktop user agents and sent a
  fake browser user agent on search requests. This is now behind `STEALTH_MODE`, default `false`. With it off, the
  browser keeps its real identity and plain HTTP requests send `hotelrate-mcp/<version>`.
  See `src/hotelrate/core/browser_policy.py`.
- **Quotes are for the requested dates.** Upstream moved Booking and Agoda stays forward by up to 12 days when the
  requested date had no price. The default is now 0, so a date with no price is reported as such.

Still open, each needs a decision (section 12.9):

- **Logged-in sessions.** Upstream's `setup` signs in to each site so member-only prices appear. Member prices are not
  publicly listed rates. Proposal: do not sign in.
- **Search-engine scraping.** URL discovery scrapes DuckDuckGo, Yahoo and Brave result pages. Proposal: use it only
  to suggest candidates during study set-up, then crawl only confirmed URLs.
- **Text-fallback offers.** When structured parsing fails, upstream returns prices found by pattern matching on page
  text, with no room or rate terms. Proposal: never store these as observations; log them as a parser failure.
- **Blocking.** With masking off, sites may block more often. No live crawl has been run yet from any location, so
  the real success rate is unknown until the canary runs.

### 12.5 Revised phase 1

Done:

- Upstream imported unmodified in its own commit, then the rule changes in 12.4 with tests.
- `.mcp.json` registers the MCP server for Claude Code.

Next, in order:

1. A `RateCollector` adapter over the imported collectors: typed outcomes (`Blocked`, `NotFound`, `ParseError`,
   `SourceError`), confirmed URLs only, text-fallback offers rejected. Done for Google Hotels (12.6); the imported
   Playwright collectors are not adapted.
2. ~~The shared policy from section 4 (pauses, retry cap, block pause, once-per-day ledger, raw capture) wrapped
   around every collector call.~~ Done (12.6, 12.7).
3. ~~The section 5 schema as Alembic migrations, replacing `sql/schema.sql`.~~ Done (12.7); `sql/schema.sql` stays for
   the imported worker.
4. ~~The Google Hotels price calendar through the `google-hotels` library.~~ Done, with channel offers (12.6).
5. Recorded-response fixtures (done) and a first live canary from an office machine (not done).

Phases 2 to 5 keep their goals. `apps/worker` and `apps/api` are the starting points for phases 2 and 4.

Layout: the package stays at `src/hotelrate/` instead of `backend/rateshopper/` (section 9), so upstream fixes can
still be merged. This can be renamed later if tracking upstream stops mattering.

### 12.6 Google Hotels collector (built 2026-11-10)

`src/hotelrate/rateshop/` implements `resolve_hotel`, `price_calendar` and `channel_offers` from section 4 on
`google-hotels==0.1.0`, with the library's transport replaced (finding F2) so every call goes through
`RequestPolicy`, our retry cap and block pause. Library failures map to `Blocked`, `NotFound`, `ParseError` (with
the raw answer) and `SourceError`. No-price dates are filled in (F3) and rate plans derived from the flags (F4).
`rate_shop()` runs a calendar plus offers dates, one outcome per date. Tests replay real answers recorded in
`tests/fixtures/google_hotels/`; `tests/test_google_hotels_contract.py` pins the library internals used.

Live results from this cloud session, honest `hotelrate-mcp` user agent, `gl=gb`, GBP:

| Call | Result |
|---|---|
| Search "The Savoy London" | 1.3 s; correct hotel first |
| 365-day calendar, The Savoy | 0.3 s; 300 to 315 priced days, horizon 2027-10-14, about 10 no-price days inside it |
| Offers, Strand Palace | 1.0 s; 21 channels incl. Booking.com, Agoda, Trip.com, Hotels.com; no room-level rates |
| Offers, The Savoy, 3 dates | 1 channel on one date; on another the official site with room-level SAVER and FLEX rates |
| Full shop, The Savoy: calendar plus 3 offers dates | 4 requests, 19.7 s with the default pauses |

Open points from these runs:

- **Coverage varies by hotel and date.** Room names, cancellation and breakfast come back only for some channels.
  With the honest user agent the search answer was smaller than with a browser user agent, so Google may send
  less to non-browser clients; this was not compared further.
- **Run size.** For 11 hotels, calendars are 11 calls (about a minute with pauses) and offers on 94 sample dates are
  about 1,030 calls (about 1.5 hours at the default pauses). Lower pauses shorten this; the daily canary should
  show whether that holds before they are lowered.
- **Cloud IP.** These calls succeeded from a cloud data-centre IP. The office worker (section 3) is still the
  expected main collector.

### 12.7 Store, study runs and report (built 2026-11-11)

**Store** (`src/hotelrate/store/`): SQLAlchemy models and Alembic migration `0001` for `hotels`, `studies`,
`study_hotels`, `events`, `runs`, `fetch_ledger`, `rate_observations` and `raw_responses` (section 5), plus
`run_fetches`, which records every fetch a run used, including ones reused from earlier that day.
`rate_observations` is append-only through database triggers (UPDATE, DELETE and, on PostgreSQL, TRUNCATE are
rejected) rather than revoked privileges, so it holds in both databases and for every role. SQLite is the default for
one person on one machine; PostgreSQL is supported and tested in CI. `jobs`, `job_log`, `canary_results` and
`alerts_sent` wait for the phase 2 worker.

**Runs** (`store/runner.py`): in-process, no queue yet. For each hotel, the price calendar for each study night
length, then channel offers on the sample weeks (section 6) within the calendar's horizon plus every day of each event
in the study's city. Every fetch claims its once-per-day ledger key first. An `ok` key is reused, a failed key is
retried, and a key another process claimed in the last hour is skipped. Each fetch commits on its own. Raw answers
are kept for failures by default (`STORE_RAW_RESPONSES`).

**Report** (`src/hotelrate/report/`): the six sections of section 7 as functions over the stored data, rendered to
the terminal and to a one-tab Excel file in the house format. Choices made where section 7 left room:

- Rate position uses each hotel's latest calendar; a date counts when the target and at least one comp are priced.
  The premium compares the target's average with the average of the daily comp medians.
- Event response: the baseline is the same weekday 7 and 14 days either side, leaving out other event days.
- Parity: each channel's lowest price is compared with the own site's lowest price on the same cancellation and
  breakfast terms when both list rooms, otherwise with the own site's lowest price.
- Room ladder: the median over sample dates of each room's lowest price, own-site rooms first. Some hotels list rate
  plans as rooms (The Savoy: "SUPERIOR Queen ... FLEX RO", "... SAVER BB"), so the ladder shows them as Google does.
- Sell-out signals: one row per hotel and date without a price inside the horizon, with how many hotels were dark
  that date. Per-hotel counts are in the coverage table.

**Live test** (cloud session, 3 London hotels, one sample week, default pauses): 21 requests in 110 seconds, every
fetch succeeded, 321 to 332 priced calendar days per hotel and 35 to 42 booking channels per hotel. A second run the
same day sent no requests.

**Run size, measured:** about 5 seconds per request at the default pauses. An 11-hotel study with 12 sample weeks is
about 850 requests, roughly 75 minutes.

### 12.8 Web front end (built 2026-11-11)

`hotelrate-mcp web` serves a FastAPI app (`src/hotelrate/apps/web/`) on `127.0.0.1:8765`, with a single-page front
end in plain HTML, CSS and JavaScript (no build step). At the owner's request it is laid out as a dark analyst
console with map views rather than in the house document style; the Excel export keeps the house style. It covers hotel search, a live forward view
of any hotel (one calendar call), compset creation and membership, starting a collection and following its
progress, and the compset dashboard. That dashboard has forward averages over the next 7 to 365 days, the target
against the comp-set median, a nightly-rate chart with the comp-set range and a hover tooltip, and every report table
plus the Excel download.

Forward averages: the average nightly rate over the priced check-in dates in each window, starting at the latest
calendar snapshot. "Target vs comp set" uses only dates on which the target and at least one comp are priced; the
per-hotel table uses each hotel's own priced dates, and its comp-set row averages the daily comp median. The Excel
report gains the same three forward tables.

**Maps.** Leaflet 1.9.4 (BSD-2) is vendored under `static/vendor/leaflet/`, so no code loads from a CDN. Google
search answers carry only a shared viewport box, not per-hotel coordinates (checked on the recorded answers), so a
hotel is placed with one prices call (`GoogleHotelsCollector.locate`) and its coordinates and address are stored on
the `hotels` row; channel-offers fetches fill them in too. Search results are located one at a time after they are
listed, and the stored position is reused from then on. Distances are great-circle (haversine). **Sweep nearby**
runs Google's own "hotels near <target>" search and maps the results as candidates. The basemap is OpenStreetMap data from
OpenFreeMap's public vector tiles (dark, and positron for the light option), drawn by MapLibre GL 5.8.0 (BSD-3) through
the maplibre-gl-leaflet bridge 0.1.3 (ISC), both vendored. Tried and dropped: CARTO's dark basemap (now returns an
"API key required" watermark) and OpenStreetMap's own raster tiles (returned 403 "Access blocked" when served from a
Codespaces address, under its tile usage policy). A raster server can still be configured with `MAP_TILE_URL`.

This stands in for the phase 4 React app for one user. It has no sign-in and binds to localhost by default; a
collection started in the browser runs inside the server process and stops if the server stops (the ledger keeps
what was collected, so starting again resumes).

### 12.9 Further decisions needed

11. **Masking:** keep `STEALTH_MODE` off? Turning it on is a legal and terms-of-use question, not a technical one.
12. **Sign-in:** confirm anonymous sessions only, no member prices.
13. **Sites:** which of Booking.com, Agoda, Trip.com and Google Hotels are in scope? I recommend Google Hotels and
    Booking.com first. Trip.com is the least relevant to a UK compset and upstream reports it often returns nothing
    without a signed-in session.
14. **Calendar source:** keep the `google-hotels` library for the price calendar (12.3)?
15. **Currency default:** the imported code defaults to USD and `en-US`; section 11 item 8 proposes GBP and UK
    English. I will change the defaults once item 8 is confirmed.

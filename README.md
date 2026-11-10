# Compset Rate Shopper

Internal tool that collects publicly listed hotel rates for a target hotel and its competitive set, stores every
snapshot, and produces a competitive pricing report.

Status: early build. The code base is [hotelrate-crawl](https://github.com/seanbabalala/hotelrate-crawl) 0.3.0,
imported under its MIT license (see [UPSTREAM.md](UPSTREAM.md)). The design, the plan and the open decisions are in
[docs/DESIGN.md](docs/DESIGN.md); section 12 covers how the imported code fits the plan and what is still missing.

## What works today

**Google Hotels rate shop (no browser, live data).** Calls the same data endpoints Google Hotels' own page uses,
through the `google-hotels` library, under this project's request policy (2 requests in flight, 3 to 8 seconds
apart, at most 3 retries, a pause on any block, honest user agent, no proxies).

- Price calendar: the price of the same stay for every check-in date, about 300 days ahead, in one call
  (0.3 seconds when tested). Dates inside Google's horizon with no price are marked `no_price`; dates beyond it
  `not_offered`.
- Channel offers: every booking channel's price for one stay (Booking.com, Expedia, Agoda, Trip.com, the hotel's own
  site...), with room-level rates, free cancellation and breakfast when Google lists them.
- A full shop of one hotel (365-day calendar plus offers on 3 dates) took 4 requests and 20 seconds live.

```bash
hotelrate-mcp find "The Savoy" --city London
```

```bash
hotelrate-mcp shop <source_id> --days 365 --offers sample --csv-dir out/
```

MCP tools: `google_find_hotel`, `google_price_calendar`, `google_hotel_offers`, `google_rate_shop`. These always
call Google live; `DEMO_MODE` does not apply to them.

**Imported multi-site crawler.** `hotel_quote`, `hotel_quote_batch`, `list_platforms` and `health_check` quote one
hotel and one stay on Booking.com, Agoda, Trip.com and Google Hotels by driving Chromium through Playwright. Demo mode
(the default) returns synthetic prices. Live mode has not yet been run from this project.

Not built yet: studies, the database schema, scheduled runs, the report and Excel export, the web app.

## Set up

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
```

```bash
.venv/bin/pytest tests -q
```

```bash
.venv/bin/hotelrate-mcp doctor
```

## Use from Claude Code

`.mcp.json` registers the server as `hotelrate`. It needs the `hotelrate-mcp` command on the PATH, so install the
package first (for a cloud session, add `pip install -e .` to the environment's setup script). Then ask, for
example, "compare prices for the Park Hyatt Tokyo on 1 May".

## Collection rules

The crawler identifies itself honestly and quotes exactly the dates requested. Masking of browser automation is
behind `STEALTH_MODE` and is off by default; see section 12.4 of the design before changing it. Copy
`.env.example` to `.env` to change settings.

Upstream's own documentation is kept unmodified in [docs/upstream/](docs/upstream/).

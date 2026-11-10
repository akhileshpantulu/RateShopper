# Compset Rate Shopper

Internal tool that collects publicly listed hotel rates for a target hotel and its competitive set, stores every
snapshot, and produces a competitive pricing report.

Status: early build. The code base is [hotelrate-crawl](https://github.com/seanbabalala/hotelrate-crawl) 0.3.0,
imported under its MIT license (see [UPSTREAM.md](UPSTREAM.md)). The design, the plan and the open decisions are in
[docs/DESIGN.md](docs/DESIGN.md); section 12 covers how the imported code fits the plan and what is still missing.

## What works today

- `hotelrate-mcp`: an MCP server that quotes one hotel and one stay across Booking.com, Agoda, Trip.com and Google
  Hotels. Tools: `hotel_quote`, `hotel_quote_batch`, `list_platforms`, `health_check`.
- Demo mode (the default) returns synthetic prices with no browser, so the whole path can be tried offline.
- Live mode drives Chromium through Playwright. It has not yet been run from this project.

Not built yet: studies, the price calendar, scheduled runs, the report and Excel export, the web app.

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

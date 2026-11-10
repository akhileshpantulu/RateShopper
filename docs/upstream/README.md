<div align="center">

# hotelrate-mcp

**Real-time hotel price comparison across 4 OTA platforms — powered by MCP**

Ask any AI agent: *"Compare prices for Park Hyatt Tokyo"* — it queries
Trip.com, Booking.com, Agoda & Google Hotels in parallel and returns normalized results.

[![CI](https://github.com/seanbabalala/hotelrate-crawl/actions/workflows/ci.yml/badge.svg)](https://github.com/seanbabalala/hotelrate-crawl/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-compatible-5a67d8?logo=data:image/svg+xml;base64,PHN2ZyB4bWxucz0iaHR0cDovL3d3dy53My5vcmcvMjAwMC9zdmciIHZpZXdCb3g9IjAgMCAyNCAyNCI+PHBhdGggZD0iTTEyIDJMMiA3bDEwIDUgMTAtNS0xMC01ek0yIDE3bDEwIDUgMTAtNS0xMC01LTEwIDV6TTIgMTJsMTAgNSAxMC01LTEwLTUtMTAgNXoiIGZpbGw9IndoaXRlIi8+PC9zdmc+)](https://modelcontextprotocol.io/)
[![License: MIT](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

[Quick Start](#quick-start) · [MCP Tools](#mcp-tools) · [Live Mode](#live-mode-setup) · [Architecture](#architecture) · [Contributing](CONTRIBUTING.md)

</div>

---

## Why hotelrate-mcp?

Hotels show different prices on different OTA platforms — sometimes with **20-40% variance** for the exact same room. This project lets AI agents (or scripts) compare prices in real-time:

| | Trip.com | Booking.com | Agoda | Google Hotels |
|---|:---:|:---:|:---:|:---:|
| Real-time crawling | ✅ | ✅ | ✅ | ✅ |
| Session persistence | ✅ | ✅ | ✅ | — |
| Member-only pricing | ✅ | ✅ | ✅ | — |

**Two modes:**
- **Demo mode** — instant synthetic data, zero setup, no browser needed
- **Live mode** — real Playwright crawls against actual OTA websites

---

## Quick Start

### 1. Install

```bash
git clone https://github.com/seanbabalala/hotelrate-crawl.git
cd hotelrate-crawl
pip install -e .
```

### 2. Configure

<table>
<tr>
<td width="50%">

**Interactive wizard** (recommended)

```bash
hotelrate-mcp setup
```

Walks you through everything:
dependency checks → `.env` creation →
platform selection → browser login →
session verification

</td>
<td width="50%">

**Demo mode** (instant, no browser)

```bash
cp .env.example .env
```

`DEMO_MODE=true` by default —
returns synthetic data so you can
test the full pipeline immediately

</td>
</tr>
</table>

### 3. Run

```bash
hotelrate-mcp                # stdio (Claude Code / Claude Desktop)
hotelrate-mcp serve --transport sse --port 8080   # SSE (web clients)
```

### 4. Verify

```bash
hotelrate-mcp doctor
```

```
  Prerequisites
    ✓ Python ≥3.12 — Python 3.13.3
    ✓ Playwright package — installed
    ✓ Chromium browser — found

  Configuration
    ✓ .env file — exists
    ! Settings — DEMO_MODE=true (set to false for live crawls)

  Platform Profiles
    ✓ Trip.com profile — .playwright/trip-profile/ (16494 files)
    ✓ Booking.com profile — .playwright/booking-profile/ (53375 files)
    ✓ Agoda profile — .playwright/agoda-profile/ (13688 files)

  8 passed  •  0 error(s)  •  1 warning(s)
```

---

## Connect to AI Agents

<table>
<tr>
<td width="50%">

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "hotelrate": {
      "command": "hotelrate-mcp",
      "env": { "DEMO_MODE": "true" }
    }
  }
}
```

</td>
<td width="50%">

### Claude Code

```bash
claude mcp add hotelrate -- hotelrate-mcp
```

Then ask naturally:

> *"Compare prices for Park Hyatt Tokyo*
> *on May 1st across all platforms"*

</td>
</tr>
</table>

---

## MCP Tools

### `hotel_quote`

Get real-time hotel prices from one or more OTA platforms.

```yaml
hotel_name: "Park Hyatt Tokyo"    # required
checkin:    "2026-05-01"           # default: 7 days from now
checkout:   "2026-05-02"           # default: checkin + 1
city:       "Tokyo"                # helps URL discovery
adults:     2                      # 1-8
children:   0                      # 0-6
currency:   "USD"                  # ISO 4217
platforms:  ["trip", "booking"]    # default: all enabled
locale:     "en-US"                # BCP 47
```

### `hotel_quote_batch`

Query multiple hotels in parallel.

```yaml
hotels:
  - hotel_name: "Park Hyatt Tokyo"
    city: "Tokyo"
    checkin: "2026-05-01"
    checkout: "2026-05-02"
  - hotel_name: "The Peninsula Shanghai"
    city: "Shanghai"
concurrency: 3   # 1-10
```

### `list_platforms`

List supported OTA platforms and their current status.

### `health_check`

Check Playwright, Firecrawl, and platform readiness.

---

## CLI Commands

```
hotelrate-mcp                 Start the MCP server (default)
hotelrate-mcp serve           Start the MCP server (explicit)
hotelrate-mcp setup           Interactive first-time setup wizard
hotelrate-mcp doctor          Environment health diagnostics
```

<details>
<summary><b>hotelrate-mcp setup</b> — options</summary>

```bash
hotelrate-mcp setup                          # full interactive wizard
hotelrate-mcp setup --platforms trip booking  # specific platforms only
hotelrate-mcp setup --skip-login             # skip browser login (CI)
hotelrate-mcp setup --timeout 600            # adjust login wait time
```

7 steps: Python check → Playwright install → `.env` creation → platform selection → browser login → session probe → summary. Ctrl+C safe at any point.

</details>

<details>
<summary><b>hotelrate-mcp doctor</b> — options</summary>

```bash
hotelrate-mcp doctor          # quick check
hotelrate-mcp doctor --probe  # also verify live sessions via headless browser
hotelrate-mcp doctor --json   # machine-readable JSON (for CI / scripting)
```

Exit code: `0` = healthy, `1` = errors found.

</details>

<details>
<summary><b>Transports</b></summary>

| Transport | Command | Use Case |
|-----------|---------|----------|
| stdio | `hotelrate-mcp` | Claude Code, Claude Desktop |
| SSE | `hotelrate-mcp serve --transport sse` | Web clients, remote access |
| Streamable HTTP | `hotelrate-mcp serve --transport streamable-http` | HTTP-based MCP clients |

Backward compat: `hotelrate-mcp --transport sse` (without `serve`) still works.

</details>

---

## Live Mode Setup

Demo mode works out of the box. For **real OTA prices**, you need to log in to each platform once so browser session cookies are saved.

**Recommended:**

```bash
hotelrate-mcp setup
```

<details>
<summary>Manual setup (alternative)</summary>

1. Edit `.env`:
   ```dotenv
   DEMO_MODE=false
   PLAYWRIGHT_BROWSER_CHANNEL=chrome
   ```

2. Install browser: `playwright install chrome`

3. Log in to each platform:
   ```bash
   python scripts/platform_login_warmup.py --platform trip
   python scripts/platform_login_warmup.py --platform booking
   ```

4. Close browser after login — session cookies are saved to `.playwright/` profiles.

5. Verify: `hotelrate-mcp doctor --probe`

See [docs/live-mode-login.md](docs/live-mode-login.md) for the full walkthrough.

</details>

---

## Configuration

All via environment variables or `.env` file:

| Variable | Default | Description |
|----------|---------|-------------|
| `DEMO_MODE` | `true` | `true` = synthetic data, `false` = live Playwright crawls |
| `HEADLESS` | `true` | Show / hide browser windows |
| `GOOGLE_SKIP` | `true` | Skip Google Hotels (slower, often blocked) |
| `FIRECRAWL_ENABLED` | `true` | Enable Firecrawl for URL discovery |
| `DEFAULT_CHECKIN_OFFSET_DAYS` | `7` | Default check-in offset from today |
| `DEFAULT_LENGTH_OF_STAY` | `1` | Default number of nights |

> See [`.env.example`](.env.example) for the complete list (~30 variables).

---

## Scripting & REST API

<details>
<summary><b>CLI script</b></summary>

```bash
python scripts/live_meta_quote.py \
  --hotel "Park Hyatt Tokyo" --city "Tokyo" \
  --checkin 2026-05-01 --checkout 2026-05-02 \
  --currency USD --out reports/quote.json
```

</details>

<details>
<summary><b>REST API</b> (optional FastAPI server)</summary>

```bash
pip install -e ".[api]"
uvicorn hotelrate.apps.api.main:app --reload

curl -X POST http://127.0.0.1:8000/meta/quote \
  -H "Content-Type: application/json" \
  -d '{"hotel_name":"Park Hyatt Tokyo","city":"Tokyo","checkin":"2026-05-01","checkout":"2026-05-02"}'
```

</details>

---

## Architecture

```
src/hotelrate/
├── apps/
│   ├── mcp/               MCP Server (main interface)
│   │   ├── server.py      FastMCP server + CLI routing
│   │   ├── tools.py       4 MCP tool definitions
│   │   └── cli/           setup & doctor subcommands
│   ├── api/               FastAPI REST endpoints (optional)
│   └── worker/            APScheduler periodic crawler (optional)
├── collectors/
│   ├── trip/              Trip.com adapter
│   ├── booking/           Booking.com adapter
│   ├── agoda/             Agoda adapter
│   └── google_hotels/     Google Hotels adapter
├── services/
│   └── live_quote_service.py   Core orchestration
├── schemas/               Pydantic models
├── core/                  Normalizer, matcher, locale helpers
├── infra/                 Database layer (SQLAlchemy, optional)
└── config.py              Settings (pydantic-settings)
```

---

## Development

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[all,dev]"

pytest tests/ -v          # 112 tests
ruff check src/ tests/    # lint
hotelrate-mcp doctor      # environment health
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full guide, including how to add a new OTA platform.

---

<div align="center">

**Do not commit** `.env`, `.playwright/`, or `reports/*_debug_*` files.
Browser login state is local-only — not portable across machines.

Report security issues via [SECURITY.md](SECURITY.md).

[MIT License](LICENSE)

</div>

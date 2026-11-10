---
name: hotel-meta-live-quote
description: >
  Real-time multi-platform hotel price comparison via MCP Server and CLI.
  Supports Trip.com, Booking.com, Agoda, and Google Hotels.
  Trigger when the user wants live hotel pricing, OTA crawl operations,
  or hotel rate comparisons.
files:
  - src/hotelrate/apps/mcp/server.py
  - src/hotelrate/apps/mcp/tools.py
  - src/hotelrate/apps/mcp/preflight.py
  - src/hotelrate/services/live_quote_service.py
  - src/hotelrate/schemas/live_quote.py
  - src/hotelrate/config.py
  - scripts/live_meta_quote.py
---

# Hotel Meta Live Quote

Multi-platform hotel price comparison skill. Returns real-time room rates from Trip.com, Booking.com, Agoda, and Google Hotels.

## What This Skill Does

- Fetches live hotel room prices from 4 OTA platforms simultaneously
- Compares rates across platforms for the same hotel and dates
- Supports batch queries for multiple hotels
- Provides structured data: room names, prices, taxes, meal plans, cancellation policies
- Works in demo mode (synthetic data, no browser needed) or live mode (real Playwright crawls)

## First-Use Setup Check

**Always call `health_check` before your first `hotel_quote` or `hotel_quote_batch` call.**

If `health_check` returns a non-empty `issues` list, guide the user through the
`setup_commands` before proceeding with live crawls:

1. Call `health_check` and inspect the `issues` array.
2. If there are items with `"level": "error"`, present each issue's `message` to the user and ask them to run the corresponding `fix_command` in their terminal.
3. The `setup_commands` field collects all error/warning-level fix commands in order — you can show them as a single setup block.
4. After the user has run the fix commands, call `health_check` again to confirm everything is resolved.
5. In **demo mode** (`demo_mode: true`), issues are informational only and do not block `hotel_quote` calls.
6. In **live mode** (`demo_mode: false`), `hotel_quote` and `hotel_quote_batch` will refuse to run if there are unresolved error-level issues and return a friendly error with the fix commands.

## Available MCP Tools

### `hotel_quote` — Single Hotel Price Lookup

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `hotel_name` | string | *required* | Hotel name (e.g. "Park Hyatt Tokyo") |
| `checkin` | string | +7 days | Check-in date `YYYY-MM-DD` |
| `checkout` | string | checkin+1 | Check-out date `YYYY-MM-DD` |
| `city` | string | "" | City for URL discovery |
| `adults` | int | 2 | Number of adults (1-8) |
| `children` | int | 0 | Number of children (0-6) |
| `currency` | string | "USD" | ISO 4217 currency code |
| `platforms` | list | all | `["trip","booking","agoda","google_hotels"]` |
| `locale` | string | "en-US" | BCP 47 locale |

### `hotel_quote_batch` — Multi-Hotel Batch Query

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `hotels` | list | *required* | List of hotel query dicts (same fields as `hotel_quote`) |
| `concurrency` | int | 3 | Parallel hotel queries (1-10) |

### `list_platforms` — Supported Platforms

No parameters. Returns platform names, enabled status, and notes.

### `health_check` — System Status

No parameters. Returns Playwright, Firecrawl, platform readiness, and any
preflight issues detected at startup. Check `issues` and `setup_commands` to
guide the user through environment setup.

## CLI Commands

### Single Hotel Quote
```bash
python scripts/live_meta_quote.py \
  --hotel "Park Hyatt Tokyo" \
  --city "Tokyo" \
  --checkin 2026-05-01 \
  --checkout 2026-05-02 \
  --currency USD \
  --out reports/live_quote_latest.json
```

### Batch Smoke Test
```bash
python scripts/live_meta_quote.py \
  --batch-config scripts/live_all_sites_smoke.json \
  --batch-concurrency 2 \
  --enable-google \
  --out reports/live_all_sites_smoke_live.json
```

### MCP Server (stdio)
```bash
hotelrate-mcp
```

## Input Parameters Reference

- **hotel_name**: Use the official English hotel name for best results across platforms
- **city**: Optional but improves URL discovery significantly
- **checkin/checkout**: ISO 8601 dates; defaults use `DEFAULT_CHECKIN_OFFSET_DAYS` from settings
- **platforms**: Omit to query all enabled platforms; Google Hotels is skipped by default (`GOOGLE_SKIP=true`)
- **currency**: Affects displayed prices; some platforms may still show local currency
- **locale**: Affects language of room names and policies; use BCP 47 format (e.g. `zh-CN`, `ja-JP`)

## Output Format

```json
{
  "generated_at": "2026-05-01T12:00:00Z",
  "request": { "hotel_name": "Park Hyatt Tokyo", "checkin": "2026-05-01", ... },
  "results": [
    {
      "platform": "booking",
      "selected_url": "https://www.booking.com/hotel/jp/park-hyatt-tokyo.html",
      "offer_count": 5,
      "structured_offer_count": 5,
      "diagnosis": "structured offers captured",
      "offers": [
        {
          "room_name": "Park King Room",
          "final_price": "650.00",
          "base_price": "590.00",
          "tax_fee": "60.00",
          "currency": "USD",
          "meal_plan": "Breakfast included",
          "cancellation_policy": "Free cancellation",
          "source_url": "https://...",
          "mode": "parsed"
        }
      ]
    }
  ]
}
```

Key fields:
- `structured_offer_count` > 0 = high-confidence parsed data
- `mode: "parsed"` = structured API extraction; `"text_fallback"` = page text price extraction
- `diagnosis` = human-readable crawl outcome summary

## Platform Notes

- **Trip.com**: Best results with a warmed-up session profile (`scripts/platform_login_warmup.py --platform trip`). Anti-bot can block unauthenticated requests.
- **Booking.com**: May stall on consent pages. Warm the profile first if `pipl_consent` appears. Supports date-window probing for better availability.
- **Agoda**: Supports date-window probing. Text fallback offers are filtered out by default for higher accuracy.
- **Google Hotels**: Slowest and most fragile. Serialized in batch mode. Skipped by default (`GOOGLE_SKIP=true`). Pass `platforms=["google_hotels"]` to force.

## Troubleshooting Quick Reference

| Symptom | Action |
|---------|--------|
| `structured_offer_count=0` everywhere | Check `DEMO_MODE=true` for quick test, or warm up browser profiles |
| Trip returns empty | Run `scripts/platform_login_warmup.py --platform trip`, then `scripts/trip_session_probe.py` |
| Booking consent stall | Run `scripts/platform_login_warmup.py --platform booking` |
| Google times out | Normal — Google Hotels is fragile. Use other 3 platforms as primary |
| All platforms fail | Check Playwright installed: `playwright install chromium` |

## Configuration

Key `.env` variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `DEMO_MODE` | `true` | `true` = synthetic data, no browser. `false` = live crawls |
| `HEADLESS` | `true` | `false` = visible browser (debugging) |
| `GOOGLE_SKIP` | `true` | Skip Google Hotels in default platform list |
| `FIRECRAWL_ENABLED` | `true` | Enable Firecrawl fallback for URL discovery |
| `DEFAULT_CHECKIN_OFFSET_DAYS` | `7` | Days from today for default check-in |
| `DEFAULT_LENGTH_OF_STAY` | `1` | Default nights |

## References

- Command cheat-sheet: [references/commands.md](references/commands.md)
- Platform troubleshooting: [references/troubleshooting.md](references/troubleshooting.md)

## Guardrails

- Do not commit `.playwright/`, runtime `reports/*.json`, or local debug artifacts.
- Treat session warmup as local machine state, not portable repository state.
- Keep Google in the existing slower lane during batch runs.
- Prefer the repo's existing scripts and services over inventing one-off wrappers.

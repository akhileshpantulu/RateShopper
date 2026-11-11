# Compset Rate Shopper

Internal tool that collects publicly listed hotel rates for a target hotel and its competitive set, stores every
snapshot, and produces a competitive pricing report.

Status: phase 1 working from the command line. The code base is
[hotelrate-crawl](https://github.com/seanbabalala/hotelrate-crawl) 0.3.0, imported under its MIT license (see
[UPSTREAM.md](UPSTREAM.md)). The design, the plan and the open decisions are in [docs/DESIGN.md](docs/DESIGN.md);
section 12 covers what is built and what is still missing.

## Where it runs

It is a Python program run from a terminal: on a laptop, an office PC or a server. It is not a website and cannot be
hosted on GitHub Pages, which only serves static files and cannot run Python or call Google. A web app with sign-in
is phase 4 of the plan (docs/DESIGN.md section 10).

By default everything is stored in one SQLite file, `data/rateshopper.db`, next to where you run it, and Excel
reports go to `data/reports/`. Several people sharing results need a shared PostgreSQL database instead
(`STORE_DATABASE_URL`, below).

## Install

Needs Python 3.12 or newer and Git.

macOS or Linux:

```bash
git clone https://github.com/akhileshpantulu/RateShopper.git && cd RateShopper
```

```bash
python3 -m venv .venv && .venv/bin/pip install -e .
```

```bash
source .venv/bin/activate
```

Windows (PowerShell):

```powershell
git clone https://github.com/akhileshpantulu/RateShopper.git; cd RateShopper
```

```powershell
py -3.12 -m venv .venv; .venv\Scripts\pip install -e .
```

```powershell
.venv\Scripts\Activate.ps1
```

Copy `.env.example` to `.env` and set at least the market and currency, for UK prices:

```dotenv
GOOGLE_HOTELS_COUNTRY=gb
DEFAULT_CURRENCY=GBP
```

## Run a rate shop

1. Create a study for the compset's city and currency:

   ```bash
   hotelrate-mcp study create "London Strand" --city London --currency GBP
   ```

2. Add the target hotel. The first command lists Google Hotels' candidates; check the right property, then add it
   with `--pick`:

   ```bash
   hotelrate-mcp study add-hotel "London Strand" --role target --search "The Savoy"
   ```

   ```bash
   hotelrate-mcp study add-hotel "London Strand" --role target --search "The Savoy" --pick 1
   ```

   Then each comp the same way, with `--role comp`.

3. Add city events, one at a time or from a CSV with columns `city,country,event_date,end_date,name`:

   ```bash
   hotelrate-mcp event add --city London --date 2026-12-31 --name "New Year's Eve"
   ```

   ```bash
   hotelrate-mcp event import events.csv
   ```

4. Collect:

   ```bash
   hotelrate-mcp run "London Strand"
   ```

   For each hotel: a 365-day price calendar for 1 and 2 nights, then every booking channel's offers on one full week
   a month plus every event date. At the default pauses each request takes about 5 seconds: roughly 75 minutes for
   11 hotels, about 2 minutes for 3 hotels and one sample week. `--calendar-only` takes about 2 minutes for 11 hotels. Running again the same day reuses what was collected; anything that failed is retried.

5. Report:

   ```bash
   hotelrate-mcp report "London Strand"
   ```

   Prints the report and writes the one-tab Excel file to `data/reports/`. Sections: overview and data coverage, rate
   position by month and day of week, event response, parity of the hotel's own site against each booking channel,
   room ladder, sell-out signals, and change since the previous snapshot (after a second run on a later day).

`hotelrate-mcp study show "London Strand"` lists the hotels, events and recent runs.

### One hotel, no study

```bash
hotelrate-mcp find "The Savoy" --city London
```

```bash
hotelrate-mcp shop <source_id> --days 365 --offers sample --csv-dir out/
```

### From Claude

`.mcp.json` registers the `hotelrate` MCP server for Claude Code; install the package first so `hotelrate-mcp` is
on the PATH. Tools: `google_find_hotel`, `google_price_calendar`, `google_hotel_offers`, `google_rate_shop`, plus
the imported multi-site `hotel_quote` tools.

## Settings

All in `.env` or environment variables; [.env.example](.env.example) lists them.

| Variable | Default | What it does |
|---|---|---|
| `GOOGLE_HOTELS_COUNTRY` | `us` | Google's market (`gb` for UK) |
| `DEFAULT_CURRENCY` | `USD` | Currency for ad-hoc commands; studies set their own |
| `STORE_DATABASE_URL` | `sqlite:///data/rateshopper.db` | `postgresql+psycopg://user:password@host/db` for a shared database (install with `pip install -e ".[postgres]"`) |
| `RATESHOP_MAX_IN_FLIGHT` | `2` | Requests at once |
| `RATESHOP_PAUSE_MIN_SECONDS` / `_MAX_` | `3` / `8` | Pause between request starts |
| `STORE_RAW_RESPONSES` | `failures` | Raw Google answers kept: `none`, `failures` or `all` |

After changing `STORE_DATABASE_URL`, create the schema with `hotelrate-mcp db upgrade`. A new SQLite file is created
automatically.

## Collection rules

The collector identifies itself honestly, sends at most 2 requests at once with a random 3 to 8 second pause, retries
at most 3 times, and pauses everything on any block. There is no proxy setting. A hotel and stay is fetched at most
once a day across all studies. The imported Playwright crawler keeps browser-automation masking behind
`STEALTH_MODE`, off by default; see section 12.4 of the design before changing it.

## Develop

```bash
pip install -e ".[dev,postgres]"
```

```bash
pytest tests -q
```

Tests replay real Google answers from `tests/fixtures/google_hotels/`. The store tests run on SQLite, and also on
PostgreSQL when `TEST_DATABASE_URL` is set (CI does this). Upstream's own documentation is kept unmodified in
[docs/upstream/](docs/upstream/).

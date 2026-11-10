# Contributing to hotelrate-mcp

Thank you for your interest in contributing! This guide covers development setup, testing, and how to add new OTA platforms.

Please also follow our [Code of Conduct](CODE_OF_CONDUCT.md).

## Development Setup

```bash
# Clone the repo
git clone https://github.com/seanbabalala/hotelrate-crawl.git
cd hotelrate-crawl

# Create virtual environment (Python 3.12+)
python3 -m venv .venv
source .venv/bin/activate

# Install all dependencies (core + API + worker + dev tools)
pip install -e ".[all,dev]"

# Check environment health
hotelrate-mcp doctor

# Or run the interactive setup wizard
hotelrate-mcp setup
```

## Repo Hygiene

- Never commit `.env`, `.playwright/`, or raw `reports/*_debug_*` artifacts.
- If you need to attach debugging data to an issue or PR, sanitize tokens, session ids, and account-specific URLs first.
- Browser login profiles are local-only state. Document the warmup steps instead of sharing profile folders.

## Running Tests

```bash
# Run all tests
pytest tests/ -v

# Run only MCP server tests
pytest tests/test_mcp_server.py -v

# Run with coverage (if installed)
pytest tests/ -v --cov=hotelrate
```

## Linting

```bash
# Check for issues
ruff check src/ tests/

# Auto-fix
ruff check src/ tests/ --fix
```

## Running the MCP Server

```bash
# stdio transport (for Claude Code / Claude Desktop)
hotelrate-mcp

# SSE transport (for web clients)
hotelrate-mcp serve --transport sse --port 8080

# streamable-http transport
hotelrate-mcp serve --transport streamable-http --port 8080
```

For live OTA debugging with a real Chrome window, see [docs/live-mode-login.md](docs/live-mode-login.md).

## Project Structure

```
src/hotelrate/
├── apps/
│   ├── mcp/          # MCP Server (main interface)
│   ├── api/          # FastAPI REST API
│   └── worker/       # APScheduler worker
├── services/         # Business logic (LiveQuoteService)
├── collectors/       # Platform adapters (one per OTA)
├── schemas/          # Pydantic models
├── core/             # Normalizer, matcher, locale
├── infra/            # Database (optional)
└── config.py         # Settings
```

## Adding a New OTA Platform

To add support for a new OTA platform (e.g., Expedia):

### 1. Create the collector

```
src/hotelrate/collectors/expedia/
├── __init__.py
└── collector.py
```

Your collector must extend `BaseCollector`:

```python
from hotelrate.collectors.base import BaseCollector

class ExpediaCollector(BaseCollector):
    PLATFORM = "expedia"
    RESPONSE_PATTERNS = [...]  # URL patterns to intercept

    async def fetch_rates(self, request: SearchRequest) -> list[RawRateOffer]:
        # Implement Playwright-based scraping
        ...
```

### 2. Add the Platform enum value

In `src/hotelrate/schemas/rate.py`:

```python
class Platform(str, Enum):
    TRIP = "trip"
    BOOKING = "booking"
    GOOGLE_HOTELS = "google_hotels"
    AGODA = "agoda"
    EXPEDIA = "expedia"  # new
```

### 3. Register in collectors `__init__.py`

In `src/hotelrate/collectors/__init__.py`:

```python
from hotelrate.collectors.expedia.collector import ExpediaCollector
```

### 4. Add to LiveQuoteService

In `src/hotelrate/services/live_quote_service.py`, add the collector to the `self.collectors` dict.

### 5. Add URL discovery rules

In `src/hotelrate/services/url_discovery.py`, add platform-specific URL patterns and search strategies.

### 6. Update MCP tools

The MCP tools (`tools.py`) automatically pick up new platforms from the `Platform` enum — no changes needed unless you want platform-specific behavior.

### 7. Add tests

Create `tests/test_expedia_collector.py` with unit tests for your parser logic.

## Code Style

- Python 3.12+ type hints throughout
- Pydantic v2 for all data models
- `async/await` for all I/O operations
- 100-character line length (enforced by ruff)
- Docstrings for public methods and classes

## Commit Messages

- Use conventional commit style: `feat:`, `fix:`, `docs:`, `test:`, `refactor:`
- Keep the first line under 72 characters
- Reference issues where applicable

## Submitting Changes

1. Fork the repository
2. Create a feature branch: `git checkout -b feat/my-feature`
3. Make your changes and add tests
4. Run `pytest tests/ -v` and `ruff check src/`
5. Commit and push
6. Open a Pull Request with a clear description

## Questions?

Open an issue or start a discussion on GitHub.

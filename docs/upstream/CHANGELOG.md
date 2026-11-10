# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [0.3.0] - 2026-04-27

### Added

- **`hotelrate-mcp setup`** — Interactive 7-step setup wizard: dependency checks, `.env` creation, platform selection, browser login warmup, session verification, and summary. Supports `--platforms`, `--skip-login`, `--timeout` flags.
- **`hotelrate-mcp doctor`** — Environment health diagnostics with grouped output (Prerequisites, Configuration, Platform Profiles). Supports `--probe` for live session checks and `--json` for machine-readable output. Exit code 0/1 for scripting.
- **`hotelrate-mcp serve`** — Explicit subcommand for running the MCP server (same as bare `hotelrate-mcp`).
- New `cli/` sub-package (`ui.py`, `checks.py`, `warmup.py`, `setup.py`, `doctor.py`) with lazy imports for zero MCP server startup overhead.
- 46 new tests for all CLI modules (total: 112 tests).

### Changed

- `server.py` converted from flat argparse to subcommand routing (`serve`, `setup`, `doctor`) with full backward compatibility — `hotelrate-mcp --transport sse` still works.
- `pyproject.toml` updated with `authors`, `[project.urls]`, and Python 3.13 classifier.
- `README.md` rewritten as a polished open-source landing page with clear setup flow.
- All Chinese comments and docstrings translated to English (functional Chinese selectors in collectors preserved).
- Removed `reports/url_mapping_cache.json` from git tracking.

## [0.2.0] - 2025-04-24

### Added

- **MCP Server** — New `hotelrate-mcp` CLI that exposes hotel price comparison as an MCP server, compatible with Claude Code, Claude Desktop, Cursor, and any MCP-compatible AI agent.
  - `hotel_quote` tool — single-hotel real-time multi-platform price lookup
  - `hotel_quote_batch` tool — batch multi-hotel price comparison
  - `list_platforms` tool — list supported OTA platforms and status
  - `health_check` tool — system health and configuration check
- Three transport modes: stdio (default), SSE, streamable-http
- Progress notification bridging from `LiveQuoteService.on_progress` to MCP context
- Claude Code agent metadata (`skills/hotel-meta-live-quote/agents/claude.yaml`)
- MCP server test suite (`tests/test_mcp_server.py`) using in-memory MCP client
- MIT License
- `CONTRIBUTING.md` with development guide and new-platform walkthrough
- This `CHANGELOG.md`

### Changed

- Package renamed from `hotelrate` to `hotelrate-mcp` for PyPI
- Version bumped from 0.1.0 to 0.2.0
- `pyproject.toml` restructured:
  - `mcp[cli]` added to core dependencies
  - `fastapi`/`uvicorn` moved to `[project.optional-dependencies.api]`
  - `sqlalchemy`/`asyncpg`/`apscheduler` moved to `[project.optional-dependencies.worker]`
  - Added `[project.scripts]` entry point for `hotelrate-mcp`
- `SKILL.md` rewritten with MCP tool documentation, parameter tables, output format examples
- `README.md` rewritten as MCP-first open-source documentation
- `.env.example` reorganized with section headers and MCP-focused comments
- `.gitignore` updated with build artifacts (`dist/`, `build/`, `*.egg-info/`)
- `agents/openai.yaml` updated with enhanced MCP-aware prompt

### Unchanged

- All existing collectors (Trip.com, Booking.com, Agoda, Google Hotels)
- `LiveQuoteService` and all business logic
- Pydantic schemas
- FastAPI endpoints
- CLI scripts
- All existing tests (52 tests continue to pass)

## [0.1.0] - 2025-03-01

### Added

- Initial release with 4 OTA platform collectors
- `LiveQuoteService` for multi-platform orchestration
- FastAPI REST API with rate search, best rates, and history endpoints
- APScheduler-based worker for periodic crawling
- CLI tool (`scripts/live_meta_quote.py`) with Rich TUI dashboard
- URL discovery service (search engines + Firecrawl + Playwright)
- URL mapping cache for session persistence
- Demo mode for synthetic data without browser
- Pydantic v2 data models for all domain types
- PostgreSQL storage with SQLAlchemy async

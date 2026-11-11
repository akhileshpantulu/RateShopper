# Notes for Claude sessions on this repository

## How the owner works

- **No PowerShell or local terminal.** The owner cannot use PowerShell on their device. Never give them commands to
  run locally. Deliver things they can open in a browser: the GitHub Codespaces setup in `.devcontainer/` (one link,
  the app opens in a tab), a hosted page, or an artifact. Run commands yourself where needed.
- **One branch.** Commit and push directly to `main`. Do not create feature branches or pull requests.
- **They want a usable front end**, not command-line tools: anything new should be reachable from the web app.

## Decisions already made

- Map: OpenStreetMap data, served by OpenFreeMap's vector tiles (dark style by default) and drawn with MapLibre.
  OpenStreetMap's own tile servers blocked the app (403); do not switch back to them. Keep the map uncluttered.
- Web app style: dark analyst console with maps (`src/hotelrate/apps/web/`). Excel exports keep the house format
  (Arial 14, #16365C headers, tables from D3, zoom 55%).
- Collection rules: honest user agent, at most 2 requests in flight, 3 to 8 second pauses, at most 3 retries, no
  proxies (docs/DESIGN.md sections 4 and 12.4).

## Checks before pushing

- `ruff check src tests` and `pytest tests -q`; CI runs both on every push, with the store tests also on PostgreSQL.

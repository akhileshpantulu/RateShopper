# Troubleshooting

## Runtime

- Prefer `./.venv/bin/python`; do not assume the shell has the right interpreter.
- Firecrawl is optional for the main flow, but discovery gets better when `http://127.0.0.1:3002` is reachable.

## Trip

- If Trip redirects to sign-in, run `scripts/platform_login_warmup.py --platform trip`, then `scripts/trip_session_probe.py`.
- Persistent Trip profiles are local state. Do not treat `.playwright/trip-profile` as shareable repo content.

## Booking

- Booking can stall on consent pages. Warm the booking profile first if you see `pipl_consent` or no room payloads.
- When structured parsing is empty, inspect payloads before tweaking selectors blindly.

## Google Hotels

- Google is slower and more fragile than the other OTAs; let batch mode serialize it.
- The script already prefers system `node` for Google when available.
- Do not assume Google should reuse the shared OTA profile. Use a dedicated `PLAYWRIGHT_USER_DATA_DIR_GOOGLE_HOTELS` only when explicitly configured.

## Agoda

- Agoda and Booking both rely on probe windows. If results look empty, widen `--probe-days` or review the selected dates before changing parser code.

## Discovery

- If discovery returns mostly search pages, run `scripts/discover_platform_urls.py` first and review the `(detail)` tags.
- If Firecrawl is unavailable, expect weaker URL candidates but the main flow should still run.

## Reporting

- In user-facing summaries, always call out whether offers were structured or fallback-derived.
- Treat `text_fallback` or sparse Google supplier text as lower confidence than platform-native structured modes.

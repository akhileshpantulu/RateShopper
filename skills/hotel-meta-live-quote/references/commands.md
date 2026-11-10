# Commands

Use repo-root relative commands and prefer `./.venv/bin/python`.

## Single Quote

Run the main hotel-name-driven flow:

```bash
./.venv/bin/python scripts/live_meta_quote.py \
  --hotel "Atour Hotel Shanghai HuangPu Xintiandi Metro Station" \
  --city "Shanghai" \
  --checkin 2026-04-10 \
  --checkout 2026-04-11 \
  --currency USD \
  --out reports/live_quote_latest.json
```

Notes:
- Add `--trip-url`, `--booking-url`, `--google-url`, `--agoda-url` only when the user already has trusted detail URLs.
- Add `--platforms trip` or another single platform when isolating one OTA.
- Add `--enable-google` when Google Hotels was requested but `.env` still has `GOOGLE_SKIP=true`.

## Batch Smoke

Use the repo's curated batch config:

```bash
URL_MAPPING_CACHE_PATH=/tmp/hotelrate_all_sites_smoke_cache.json \
BOOKING_DEBUG_SAVE_JSON=true \
AGODA_DEBUG_SAVE_JSON=true \
./.venv/bin/python scripts/live_meta_quote.py \
  --batch-config scripts/live_all_sites_smoke.json \
  --batch-concurrency 2 \
  --enable-google \
  --out reports/live_all_sites_smoke_live.json
```

Artifact paths:
- `reports/live_all_sites_smoke_live.json`
- `reports/url_mapping_cache.json` unless overridden

## URL Discovery

Run discovery when detail URL selection looks weak:

```bash
./.venv/bin/python scripts/discover_platform_urls.py \
  --hotel "Atour Hotel Shanghai HuangPu Xintiandi Metro Station" \
  --city "Shanghai" \
  --checkin 2026-04-10 \
  --checkout 2026-04-11 \
  --locale en-US
```

Expected output:
- top candidates per platform
- `(detail)` vs `(search/list)` labeling

## Payload Inspection

Use this when a platform returns `structured_offer_count=0` or looks obviously wrong:

```bash
./.venv/bin/python scripts/inspect_live_payloads.py \
  --platform booking \
  --platform-hotel-id manual \
  --platform-url "https://www.booking.com/hotel/..." \
  --checkin 2026-04-10 \
  --nights 1
```

## Session Warmup

Warm up local profiles for login walls or consent gates:

```bash
./.venv/bin/python scripts/platform_login_warmup.py --platform trip --wait-seconds 300
./.venv/bin/python scripts/platform_login_warmup.py --platform booking --wait-seconds 300
./.venv/bin/python scripts/platform_login_warmup.py --platform agoda --wait-seconds 300
```

Trip follow-up:

```bash
./.venv/bin/python scripts/trip_session_probe.py
```

## Nightly

```bash
./.venv/bin/python scripts/nightly_quote_job.py
./.venv/bin/python scripts/report_nightly_summary.py
```

Artifact path:
- `reports/nightly_quote_report.json`

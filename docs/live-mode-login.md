# Live Mode Login Guide

This guide is for contributors and users who want real OTA prices instead of demo responses.

## Recommended Setup

1. Copy the config:

```bash
cp .env.example .env
```

2. Edit `.env`:

```dotenv
DEMO_MODE=false
HEADLESS=false
PLAYWRIGHT_BROWSER_CHANNEL=chrome
```

3. Install the browser channel:

```bash
playwright install chrome
```

## Warm Up Login Profiles

Run each platform warmup script and complete login manually in the opened Chrome window:

```bash
python scripts/platform_login_warmup.py --platform trip --wait-seconds 300
python scripts/platform_login_warmup.py --platform booking --wait-seconds 300
```

Optional:

```bash
python scripts/platform_login_warmup.py --platform agoda --wait-seconds 300
```

What to do in the browser:

- Complete sign-in or consent prompts.
- Wait until the hotel or account page loads normally.
- Close the browser window to flush the persistent profile to disk.

Profiles are stored locally under `.playwright/` unless you override them in `.env`.

## Verify the Session

Trip.com:

```bash
python scripts/trip_session_probe.py
```

Live quote:

```bash
python scripts/live_meta_quote.py \
  --hotel "Park Hyatt Tokyo" \
  --city "Tokyo" \
  --checkin 2026-05-01 \
  --checkout 2026-05-02 \
  --platforms trip booking
```

## Troubleshooting

- If Trip redirects back to sign-in, rerun the Trip warmup and keep the final page on a hotel detail page.
- If Booking stalls on consent, rerun the Booking warmup and accept the prompts in the same profile.
- Do not commit `.playwright/`, `.env`, or `reports/*_debug_*`.

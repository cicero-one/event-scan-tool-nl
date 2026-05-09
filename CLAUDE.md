# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What This Is

A single-file Flask MVP (`app.py`) that scans for EDM/techno/psytrance/queer events in the Netherlands, scores them, stores them in SQLite, and emails alerts. The Dutch README is authoritative for user-facing behaviour.

## Running the App

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # create this file from the env vars listed below
python app.py
```

App runs at `http://localhost:5000`. There are no tests and no linter configuration.

To trigger a scan manually without the web UI:

```python
# from a Python shell with the venv active
from app import run_scan
run_scan(send_alerts=False)
```

## Required Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `SECRET_KEY` | `dev-secret-change-me` | Flask session signing |
| `DATABASE_URL` | `sqlite:///events.db` | SQLAlchemy DB URI |
| `TICKETMASTER_API_KEY` | *(none)* | Discovery API; falls back to demo events if absent |
| `ALERT_EMAIL_TO` | `Tom.loijer@gmail.com` | Alert recipient |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM` | *(none)* | Email sending; all must be set or emails are skipped |
| `DISABLE_SCHEDULER` | `0` | Set to `1` to prevent APScheduler from starting (useful in dev/testing) |
| `SCAN_HOUR`, `SCAN_MINUTE`, `SCAN_TIMEZONE` | `10`, `0`, `Europe/Amsterdam` | Daily scan schedule |

## Architecture

Everything lives in `app.py`. The three database models are:

- **`Setting`** — single-row config (one record, always fetched via `get_settings()`): genres, price caps, match threshold, LGBTQ bonus, cities filter.
- **`Event`** — one row per unique event, keyed on `external_id` (format `"source:raw_id"`, e.g. `"ticketmaster:abc123"`). Tracks current/previous/lowest-seen price.
- **`Alert`** — created on new high-scoring events (`alert_type="match"`) or price drops (`alert_type="price_drop"`). Batched into a single email per scan run.

### Scan Pipeline

`run_scan()` → `fetch_ticketmaster_events()` (or `fetch_demo_events()` as fallback) → `upsert_event()` per item → batch email.

`upsert_event()` calls `score_event()` internally; the score is persisted on the Event row.

### Scoring (`score_event`)

Score is capped at 100 and built from:
- Genre keyword matches in name/venue/city/tags/description: up to 35 pts
- Price within budget (≤ max): 25 pts; at or below 50% of max: additional 10 pts; unknown price: 10 pts
- `lgbtq_branded`: `settings.lgbtq_bonus` pts (default 10)
- Venue present: 10 pts; start date present: 10 pts; URL present: 10 pts

Alerts fire when `match_score >= settings.min_match_score` (default 80).

### Adding a New Event Source

Implement a `fetch_<source>_events(settings: Setting) -> List[Dict]` function that returns dicts with these keys: `source`, `external_id`, `name`, `start_date`, `city`, `venue`, `url`, `genre_tags`, `lgbtq_branded`, `event_type` (`"club"` or `"festival"`), `price` (float or `None`). Then call it from `run_scan()` and merge results before the upsert loop.

### Scheduler

APScheduler starts at module import time (unless `DISABLE_SCHEDULER=1`). The scheduler runs `run_scan()` inside `app.app_context()` — this context push is required for any code that touches the DB outside a request.

### Templates

The app renders `index.html`, `settings.html`, and `alerts.html` via `render_template`. These template files are not present in the repository and must be created in a `templates/` directory for the app to serve pages.

## Production Notes (from README)

- Switch `DATABASE_URL` to Postgres.
- Run via gunicorn; move the scheduled scan to a separate worker to avoid APScheduler conflicts.
- Add deduplication on name + date + venue when multiple sources surface the same event.

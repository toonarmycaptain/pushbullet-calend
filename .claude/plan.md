## Project: pushbullet-calend

SMS reminder system: parse directives from Google Calendar event descriptions, send timed SMS via Pushbullet, track sent messages to avoid duplicates.

## Event Description Syntax

```
SMS: -30m | +61412345678 | Hey, reminder to pick up the kids at 3:15!
SMS: -1h | +61412345678 | Dinner at 7, don't forget!
```

Format: `SMS: -<time_before> | <phone_number> | <message_text>`
Time units: `m` (minutes), `h` (hours), `d` (days). Multiple SMS lines per event supported.

## Architecture

- Poll Google Calendar every ~5 minutes (cron on local machine)
- Query events in next 7 days with `singleEvents=true` (expands recurring events)
- Parse descriptions for `SMS:` lines
- Calculate send_time = event_start - offset
- If send_time <= now < event_start and not already sent → send via Pushbullet, record in SQLite
- Transient errors (network/5xx) → skip this cycle, retry next poll automatically
- Permanent errors (4xx) → record failure, increment retry count, push notification to self
- Push notification fails → log to local file as last resort

## Sent Message Tracking (SQLite + SQLAlchemy)

Table `sent_messages`: id, event_id, instance_start, phone_number, message_hash, sent_at, status, retry_count
Dedup key: (event_id, instance_start, phone_number, message_hash)
- Same recurring instance → won't re-send
- Edited description → new hash → sends updated version
- Transient failures don't record → automatic retry next cycle
- Permanent failures tracked with retry_count, give up after max_retries

## Config (TOML)

- Multiple calendar IDs supported
- Google OAuth2 credentials/token paths
- Pushbullet API key and device identifier
- Lookahead days, poll interval, DB path

## Tooling

- Python 3.14
- uv for env/package management
- ruff for linting/formatting
- pytest with parametrized tests
- pre-commit with ruff hooks

## Implementation Phases

1. ~~Parser + tests~~ ✓
2. ~~SQLite DB layer (SQLAlchemy) + tests~~ ✓
3. ~~Google Calendar client~~ ✓
4. ~~Pushbullet sender (transient/permanent error split, retry with backoff)~~ ✓
5. ~~Main orchestrator~~ ✓
6. ~~Config loading + example config~~ ✓
7. Cron setup for local machine deployment
8. ~~Error handling, logging, failure notifications~~ ✓

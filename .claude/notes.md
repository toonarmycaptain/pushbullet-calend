## Project Notes

- Repo: ~/PycharmProjects/pushbullet-calend/
- Started: 2026-04-12
- Purpose: Automate recurring SMS reminders (e.g. pickup kids, event reminders) without manually scheduling on phone each time.

## Key Decisions

- Syntax: `SMS: -30m | +phone | message text` in Google Calendar event descriptions
- Transient errors (network/5xx) are not recorded in DB — just skipped and retried next poll cycle. This means internet outages don't burn retry counts.
- Permanent errors (4xx) are recorded with retry_count, give up after max_retries (default 5).
- Pushbullet push notifications (not SMS) used for failure alerts to self.
- `singleEvents=true` expands recurring events into individual instances automatically.
- Multiple calendars supported via config.

## Pushbullet API

- SMS: `POST /v2/texts` — requires Pushbullet app on Android phone, phone must be online
- Push notifications: `POST /v2/pushes` — independent of SMS, used for failure alerts
- Device iden: `GET /v2/devices` with Access-Token header
- Pro required for >100 SMS/month

## Google Calendar API

- OAuth2 for personal calendar access
- First run opens browser for auth, saves token.json
- credentials.json from Google Cloud Console (Calendar API enabled)

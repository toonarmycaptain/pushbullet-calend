# pushbullet-calend

Automatically send SMS reminders based on Google Calendar events. Add a simple directive to any event description and the system handles the rest — including recurring events.

## How it works

A script polls your Google Calendar every few minutes, looking for events with SMS directives in their descriptions. When it's time to send, it uses the Pushbullet API to send an SMS from your phone.

## Reminder syntax

Add lines to any Google Calendar event description in this format:

```
SMS: -<time> | <phone_number> | <message>
```

Where `<time>` is how long before the event to send, using `m` (minutes), `h` (hours), or `d` (days).

### Examples

```
SMS: -30m | +61412345678 | Hey, can you pick up the kids from school at 3:15?
SMS: -1h | +61412345678 | Dinner at 7 tonight, don't forget!
SMS: -2d | +61498765432 | Football practice is on Thursday at 5pm
```

You can have multiple SMS lines in a single event — each one is sent independently:

```
SMS: -1d | +61412345678 | Reminder: school pickup tomorrow at 3:15
SMS: -30m | +61412345678 | Heading to pick up the kids now?
SMS: -1h | +61498765432 | Don't forget about tomorrow's event!
```

The message text is everything after the last `|`, so your messages can contain pipe characters if needed.

### Timing

The directive `-30m` means "30 minutes before the event starts". The message will be sent on the first poll cycle after that time arrives, as long as the event hasn't started yet.

This means you can add a `-12h` directive to a 7pm event and it will send at 7am — even if you add the directive after 7am, it'll send on the next poll cycle.

For recurring events, each occurrence is handled independently — you set it up once and every instance gets its own reminder.

## Prerequisites

- Python 3.14+
- [uv](https://docs.astral.sh/uv/) for package management
- A Google Cloud project with the Calendar API enabled
- A [Pushbullet](https://www.pushbullet.com/) account and the Pushbullet app on your Android phone
- Pushbullet Pro if sending more than 100 SMS/month

## Setup

### 1. Clone and install

```bash
git clone <repo-url>
cd pushbullet-calend
uv sync
```

### 2. Google Calendar credentials

1. Go to [Google Cloud Console](https://console.cloud.google.com/)
2. Create a project (or use an existing one)
3. Go to **APIs & Services > Library**, search for "Google Calendar API", and enable it
4. Go to **APIs & Services > Credentials > Create Credentials > OAuth client ID**
5. Choose **Desktop app**, then download the JSON file
6. Save it as `credentials.json` in the project root

### 3. Pushbullet API key

1. Go to [Pushbullet Settings](https://www.pushbullet.com/#settings)
2. Under **Access Tokens**, click **Create Access Token**
3. To find your phone's device identifier:

```bash
curl -s https://api.pushbullet.com/v2/devices \
  -H "Access-Token: YOUR_API_KEY" | python -m json.tool
```

Look for your phone's `iden` field in the output.

### 4. Configuration

```bash
cp config.example.toml config.toml
```

Edit `config.toml` with your credentials:

```toml
[google]
calendar_ids = ["primary"]
credentials_file = "credentials.json"
token_file = "token.json"

[pushbullet]
api_key = "o.your_api_key_here"
device_iden = "your_device_iden_here"

[schedule]
lookahead_days = 7

[database]
path = "sent_messages.db"
```

To monitor multiple calendars, add their IDs to the list:

```toml
calendar_ids = ["primary", "family@group.calendar.google.com"]
```

### 5. First run (OAuth authorization)

```bash
uv run pushbullet-calend
```

A browser window will open asking you to authorize calendar access. After approving, a `token.json` file is saved so you won't be asked again.

### 6. Schedule with cron

Run every 5 minutes:

```bash
crontab -e
```

Add:

```
*/5 * * * * cd /path/to/pushbullet-calend && uv run pushbullet-calend
```

## Error handling

- **Network outages / server errors** — the script silently skips and retries on the next poll cycle. Your internet can be down for hours and messages will send when it comes back.
- **Application errors** (bad phone number, invalid API key, etc.) — recorded as failed, retried up to 5 times. You'll get a Pushbullet push notification on your phone about the failure.
- **If push notifications also fail** — errors are logged to stderr.

## Deduplication

Messages are tracked in a local SQLite database to prevent duplicates. The dedup key is the combination of event ID, occurrence time, phone number, and a hash of the message text.

This means:
- The same recurring event won't send the same message twice for the same occurrence
- If you edit the message text in the event description, it'll send the updated version
- Different phone numbers in the same event each get their own message

## Development

```bash
uv sync
uv run pytest                # run tests
uv run ruff check src/ tests/  # lint
uv run ruff format src/ tests/  # format
```

Pre-commit hooks are configured to run ruff automatically on commit.

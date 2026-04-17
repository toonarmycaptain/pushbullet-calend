# pushbullet-calend

Automatically send SMS reminders based on Google Calendar events, and get texted when specific emails arrive. Add a simple directive to any event description and the system handles the rest — including recurring events.

## How it works

A daemon polls your Google Calendar every few minutes, looking for events with SMS directives in their descriptions. It calculates the exact time each message should be sent and sleeps until that moment, then uses the Pushbullet API to send an SMS from your phone.

## Reminder syntax

Add lines to any Google Calendar event description in this format:

```
SMS: -<time> | <phone_number> | <message>
```

Where `<time>` is how long before the event to send, using `m` (minutes), `h` (hours), or `d` (days).

### Examples

```
SMS: -30m | 817-555-1234 | Hey, can you pick up the kids from school at 3:15?
SMS: -1h | (817) 555-1234 | Dinner at 7 tonight, don't forget!
SMS: -2d | 8175559876 | Football practice is on Thursday at 5pm
```

You can have multiple SMS lines in a single event — each one is sent independently:

```
SMS: -1d | 817-555-1234 | Reminder: school pickup tomorrow at 3:15
SMS: -30m | 817-555-1234 | Heading to pick up the kids now?
SMS: -1h | 817-555-9876 | Don't forget about tomorrow's event!
```

The message text is everything after the last `|`, so your messages can contain pipe characters if needed.

### Phone numbers

Numbers without a `+` country code prefix are assumed to be US numbers and get `+1` prepended automatically. All formatting (dashes, spaces, dots, parentheses) is stripped.

These are all equivalent:

```
817-555-1234      → +18175551234
(817) 555-1234    → +18175551234
817.555.1234      → +18175551234
8175551234        → +18175551234
+18175551234      → +18175551234
```

For international numbers, include the `+` and country code:

```
SMS: -1h | +44 7911 123456 | Don't forget the meeting!
```

### Timing

The directive `-30m` means "30 minutes before the event starts". In daemon mode, the message is sent at the exact scheduled time. In one-shot mode, it's sent on the first run after that time arrives, as long as the event hasn't started yet.

This means you can add a `-12h` directive to a 7pm event and it will send at 7am — even if you add the directive after 7am, it'll send on the next poll or run.

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
4. Go to **APIs & Services > OAuth consent screen**, configure it:
   - Choose **External** user type
   - Fill in app name and email (required fields only)
   - Add scope: `https://www.googleapis.com/auth/calendar.readonly`
   - Add your Google email as a **test user**
5. Go to **APIs & Services > Credentials > Create Credentials > OAuth client ID**
6. Choose **Desktop app**, then download the JSON file
7. Save it as `credentials.json` in the project root

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
poll_interval_minutes = 5

[database]
path = "sent_messages.db"
```

See the [Email watch](#email-watch) section below for the optional `[email_watch]` config.

To monitor multiple calendars, add their IDs to the list:

```toml
calendar_ids = ["primary", "family@group.calendar.google.com"]
```

### 5. First run (OAuth authorization)

```bash
uv run pushbullet-calend
```

A browser window will open asking you to authorize calendar access. You'll see a "Google hasn't verified this app" warning — click **Advanced** > **Go to pushbullet-calend (unsafe)** to proceed. After approving, a `token.json` file is saved so you won't be asked again.

## Running

### One-shot mode

Runs a single poll cycle and exits. Useful for testing or running via cron.

```bash
uv run pushbullet-calend
```

### Daemon mode (recommended)

Runs continuously in the background. Polls the calendar every 5 minutes (configurable) and sends messages at the exact scheduled time.

```bash
# Run in the foreground (Ctrl+C to stop)
uv run pushbullet-calend --daemon

# Run in the background, detached from your terminal
nohup uv run pushbullet-calend --daemon >> pushbullet-calend.log 2>&1 &

# Stop the background daemon
pkill -f "pushbullet-calend --daemon"
```

The daemon responds to `SIGINT` and `SIGTERM` for clean shutdown.

### Cron (alternative to daemon)

If you prefer cron over a daemon, messages will be sent within 5 minutes of the scheduled time (depending on your cron interval):

```bash
crontab -e
```

Add:

```
*/5 * * * * cd /path/to/pushbullet-calend && /path/to/pushbullet-calend/.venv/bin/python -m pushbullet_calend >> pushbullet-calend.log 2>&1
```

## Email watch

In addition to calendar reminders, you can monitor an email inbox and get an SMS when a matching email arrives. This is useful for time-sensitive alerts like sales, shipping notifications, etc.

### Configuration

Add an `[email_watch]` section to your `config.toml`:

```toml
[email_watch]
enabled = true
imap_server = "imap.gmail.com"            # or "outlook.office365.com" for Hotmail/Outlook
email_address = "you@gmail.com"
app_password = "<encrypted — see below>"

[[email_watch.rules]]
subject = "Your order has shipped!"
phone_number = "817-555-1234"
message = "Shipping notification just arrived!"
```

You can add multiple `[[email_watch.rules]]` blocks. Each rule matches on exact subject and sends to its own phone number.

### Encrypting your email password

Email passwords are stored encrypted in the config file. To set one up:

```bash
uv run pushbullet-calend --encrypt-password
```

This generates an encryption key at `~/.pushbullet-calend.key` (or reuses an existing one) and prints an encrypted token to paste into your `config.toml` as `app_password`.

For Gmail, you'll need an [App Password](https://myaccount.google.com/apppasswords) (requires 2FA enabled). For Outlook/Hotmail, use your regular password or an app password if 2FA is on.

### Testing your email setup

```bash
uv run pushbullet-calend --test-email
```

This connects to your inbox, searches for matching subjects, and shows what it finds — without sending any SMS.

## Error handling

- **Network outages / server errors (5xx)** — silently skipped and retried on the next cycle. Your internet can be down for hours and messages will send when it comes back. These do not count against the retry limit.
- **Application errors (4xx)** (bad phone number, invalid API key, etc.) — recorded as failed, retried up to 5 times. You'll get a Pushbullet push notification on your phone about the failure.
- **If push notifications also fail** — errors are logged to stderr/log file.

## Deduplication

Messages are tracked in a local SQLite database to prevent duplicates. The dedup key is the combination of event ID, occurrence time, phone number, and a hash of the message text.

This means:
- The same recurring event won't send the same message twice for the same occurrence
- If you edit the message text in the event description, it'll send the updated version
- Different phone numbers in the same event each get their own message

## Disclaimer

This is a personal tool built for individual use. It is not affiliated with or endorsed by Google or Pushbullet. Use at your own risk. No warranty is provided, express or implied. You are responsible for your own API usage, message content, and compliance with applicable terms of service.

## Development

```bash
uv sync
uv run pytest                   # run tests
uv run ruff check src/ tests/   # lint
uv run ruff format src/ tests/  # format
```

Pre-commit hooks are configured to run ruff automatically on commit.

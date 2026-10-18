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
SMS: -30m | 555-555-0123 | Hey, can you pick up the kids from school at 3:15?
SMS: -1h | (555) 555-0123 | Dinner at 7 tonight, don't forget!
SMS: -2d | 5555550199 | Football practice is on Thursday at 5pm
```

You can have multiple SMS lines in a single event — each one is sent independently:

```
SMS: -1d | 555-555-0123 | Reminder: school pickup tomorrow at 3:15
SMS: -30m | 555-555-0123 | Heading to pick up the kids now?
SMS: -1h | 555-555-0199 | Don't forget about tomorrow's event!
```

The message text is everything after the last `|`, so your messages can contain pipe characters if needed.

### Phone numbers

Numbers without a `+` country code prefix are assumed to be US numbers and get `+1` prepended automatically. All formatting (dashes, spaces, dots, parentheses) is stripped.

These are all equivalent:

```
555-555-0123      → +15555550123
(555) 555-0123    → +15555550123
555.555.0123      → +15555550123
5555550123        → +15555550123
+15555550123      → +15555550123
```

For international numbers, include the `+` and country code:

```
SMS: -1h | +44 7911 123456 | Don't forget the meeting!
```

### Timing

The directive `-30m` means "30 minutes before the event starts". In daemon mode, the message is sent at the exact scheduled time. In one-shot mode, it's sent on the first run after that time arrives, as long as the event hasn't started yet.

This means you can add a `-12h` directive to a 7pm event and it will send at 7am — even if you add the directive after 7am, it'll send on the next poll or run.

For recurring events, each occurrence is handled independently — you set it up once and every instance gets its own reminder.

All-day events (birthdays, holidays) start at midnight in the calendar's time zone, so `-5h` texts at 7pm the night before.

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
calendar_ids = ["owned"]
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

`calendar_ids` chooses which calendars are checked for directives. Three special values save looking up calendar IDs:

- `"owned"` (the default) — every calendar you own, e.g. sub-calendars you made to colour-code family members
- `"all"` — every calendar in your list, including ones shared with you and subscriptions (free/busy-only calendars are skipped, as they hide event descriptions)
- `"primary"` — just your main calendar

`"owned"` and `"all"` include calendars hidden from your list, and pick up calendars you add later. Mix them with explicit calendar IDs (found under the calendar's **Settings > Integrate calendar**):

```toml
calendar_ids = ["owned", "family@group.calendar.google.com"]
```

Overlapping calendars won't double-text: the same message to the same number at the same time is only sent once, however many calendars or events it comes from (see [Deduplication](#deduplication)).

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

### systemd service (recommended for Linux)

An example unit is included in the repo. Copy it, then edit the copy to set the correct paths for `WorkingDirectory` and `ExecStart` (the comments in the file explain each one):

```bash
cp pushbullet-calend.example.service pushbullet-calend.service
```

Then install it.

**As a user service** (no root required):

```bash
cp pushbullet-calend.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable pushbullet-calend
systemctl --user start pushbullet-calend

# Check status / logs
systemctl --user status pushbullet-calend
journalctl --user -u pushbullet-calend -f
```

A user service belongs to your own systemd instance, so always include `--user`: plain `systemctl status pushbullet-calend` reports "Unit pushbullet-calend.service could not be found." even while it's running.

User services run only while you're logged in. To keep it running after logout:

```bash
loginctl enable-linger $USER
```

**As a system service** (runs at boot, requires root):

```bash
sudo cp pushbullet-calend.service /etc/systemd/system/
```

Edit the installed copy to add a `User=` line under `[Service]`, and change `WantedBy=` to `multi-user.target`:

```ini
[Service]
User=youruser
...

[Install]
WantedBy=multi-user.target
```

Then:

```bash
sudo systemctl daemon-reload
sudo systemctl enable pushbullet-calend
sudo systemctl start pushbullet-calend

# Check status / logs
sudo systemctl status pushbullet-calend
journalctl -u pushbullet-calend -f
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
phone_number = "555-555-0123"
message = "Shipping notification just arrived!"
```

You can add multiple `[[email_watch.rules]]` blocks. Each rule needs at least a `subject` or `sender` (or both):

- **Subject only** — matches emails with that exact subject line.
- **Sender only** — matches any email from that address (substring match on the From header).
- **Both** — the email must match both criteria (AND logic).

For OR logic (match subject *or* sender), use two separate rules.

```toml
# Match on sender only
[[email_watch.rules]]
sender = "alerts@example.com"
phone_number = "555-555-0123"
message = "Got an email from alerts!"

# Match on both sender AND subject
[[email_watch.rules]]
subject = "Your order has shipped!"
sender = "shipping@example.com"
phone_number = "555-555-0123"
message = "Shipping notification from Example!"
```

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
- **A calendar that can't be fetched** — retried once in the same poll. If it still fails, the other calendars carry on, with that calendar's events from its last successful fetch standing in, so texts already scheduled from it still go out. At the end of the poll you get one push notification listing every calendar that failed. Only when every calendar fails (e.g. no network) does the poll count as failed: it's retried with backoff, the already-queued messages are kept, and you get a push notification.

## Deduplication

Messages are tracked in a local SQLite database to prevent duplicates. The dedup key is the combination of event ID, occurrence time, phone number, and a hash of the message text.

On top of that, the same message to the same number at the same send time is only ever sent once, however many events it comes from — an invite that's on several of your calendars, an event copied to another calendar, or two events that happen to schedule the identical text for the same moment.

This means:
- The same recurring event won't send the same message twice for the same occurrence
- If you edit the message text in the event description, it'll send the updated version
- Different phone numbers in the same event each get their own message
- Copies of an event, on one calendar or several, send each text once

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

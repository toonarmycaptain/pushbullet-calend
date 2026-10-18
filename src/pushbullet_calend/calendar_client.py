"""Google Calendar API client for fetching events."""

import logging
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import Resource, build

from pushbullet_calend.config import GoogleConfig

_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
logger = logging.getLogger(__name__)

# Special calendar_ids entries, mapped to the access roles of the calendars they select.
# Free/busy-only calendars hide event descriptions, so they can't hold SMS directives.
_CALENDAR_SELECTORS: dict[str, set[str]] = {
    "owned": {"owner"},
    "all": {"owner", "writer", "reader"},
}


@dataclass
class CalendarEvent:
    event_id: str
    summary: str
    description: str
    start: datetime


def _authenticate(config: GoogleConfig) -> Credentials:
    """Load or create OAuth2 credentials."""
    token_path = Path(config.token_file)
    creds = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), _SCOPES)

    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
        except Exception:
            logger.warning("Token refresh failed; re-authenticating")
            creds = None
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(config.credentials_file, _SCOPES)
        creds = flow.run_local_server(port=0)

    token_path.write_text(creds.to_json())
    return creds


def _event_start(start: dict[str, str], calendar_tz: str | None) -> datetime:
    """Return an event's start as an aware datetime.

    All-day events only have a date, so they start at midnight in the event's
    (or calendar's) time zone.
    """
    if "dateTime" in start:
        return datetime.fromisoformat(start["dateTime"])
    midnight = datetime.fromisoformat(start["date"])
    tz_name = start.get("timeZone") or calendar_tz
    return midnight.replace(tzinfo=ZoneInfo(tz_name)) if tz_name else midnight.astimezone()


def calendar_name(calendar: dict) -> str:
    """The calendar's name as shown in your list (which you may have renamed)."""
    return calendar.get("summaryOverride") or calendar["summary"]


def _list_calendars(service: Resource) -> list[dict]:
    """Return every calendar in the user's calendar list, hidden ones included."""
    calendars: list[dict] = []
    page_token = None
    while True:
        result = service.calendarList().list(showHidden=True, pageToken=page_token).execute()
        calendars.extend(result.get("items", []))
        page_token = result.get("nextPageToken")
        if not page_token:
            return calendars


def _calendar_names(service: Resource) -> dict[str, str]:
    """Map each calendar's ID (and "primary") to its name, or {} if the list can't be fetched."""
    try:
        calendars = _list_calendars(service)
    except Exception:
        logger.warning("Couldn't fetch calendar names", exc_info=True)
        return {}
    names = {c["id"]: calendar_name(c) for c in calendars}
    names.update({"primary": calendar_name(c) for c in calendars if c.get("primary")})
    return names


def _resolve_calendar_ids(service: Resource, calendar_ids: list[str]) -> list[str]:
    """Expand "owned" and "all" entries into the IDs of the calendars they select.

    Other entries (explicit IDs, "primary") pass through, and duplicates are dropped.
    """
    if not _CALENDAR_SELECTORS.keys() & set(calendar_ids):
        return calendar_ids
    calendars = _list_calendars(service)
    resolved: list[str] = []
    for entry in calendar_ids:
        roles = _CALENDAR_SELECTORS.get(entry)
        if roles is None:
            resolved.append(entry)
        else:
            resolved.extend(c["id"] for c in calendars if c["accessRole"] in roles)
    return list(dict.fromkeys(resolved))


def _fetch_calendar(
    service: Resource, calendar_id: str, time_min: str, time_max: str
) -> list[CalendarEvent]:
    """Fetch one calendar's events between time_min and time_max."""
    logger.info("Fetching events from calendar %s", calendar_id)
    events: list[CalendarEvent] = []
    page_token = None
    while True:
        result = (
            service.events()
            .list(
                calendarId=calendar_id,
                timeMin=time_min,
                timeMax=time_max,
                singleEvents=True,
                orderBy="startTime",
                pageToken=page_token,
            )
            .execute()
        )
        for item in result.get("items", []):
            events.append(
                CalendarEvent(
                    event_id=item["id"],
                    summary=item.get("summary", ""),
                    description=item.get("description", ""),
                    start=_event_start(start=item["start"], calendar_tz=result.get("timeZone")),
                )
            )
        page_token = result.get("nextPageToken")
        if not page_token:
            return events


def fetch_events(
    config: GoogleConfig,
    *,
    lookahead_days: int = 7,
    last_known: dict[str, list[CalendarEvent]] | None = None,
    failed: list[tuple[str, Exception]] | None = None,
) -> list[CalendarEvent]:
    """Fetch upcoming events from all configured calendars.

    A calendar that fails is retried once, then skipped. Pass *last_known* to remember
    each calendar's events between fetches: a skipped calendar's last fetched events are
    then used instead, so texts already scheduled from it still go out. Pass *failed*
    to collect (calendar name, error) for each calendar that was skipped.
    """
    creds = _authenticate(config)
    service = build("calendar", "v3", credentials=creds)

    now = datetime.now(UTC)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=lookahead_days)).isoformat()

    calendar_ids = _resolve_calendar_ids(service, config.calendar_ids)
    fetched: dict[str, list[CalendarEvent]] = {}
    errors: dict[str, Exception] = {}
    to_fetch = calendar_ids
    # Try every calendar, then retry once whichever failed.
    for _ in range(2):
        errors = {}
        for calendar_id in to_fetch:
            try:
                fetched[calendar_id] = _fetch_calendar(service, calendar_id, time_min, time_max)
            except Exception as exc:
                errors[calendar_id] = exc
        to_fetch = list(errors)

    # Every calendar failing (e.g. no network) is a failed fetch, not an empty one.
    if errors and len(errors) == len(calendar_ids):
        raise next(iter(errors.values()))
    for calendar_id, exc in errors.items():
        if last_known is not None and calendar_id in last_known:
            logger.warning(
                "Calendar %s failed, using its last fetched events: %s", calendar_id, exc
            )
            fetched[calendar_id] = last_known[calendar_id]
        else:
            logger.warning("Skipping calendar %s this poll: %s", calendar_id, exc)
    if failed is not None and errors:
        names = _calendar_names(service)
        failed.extend(
            (names.get(calendar_id, calendar_id), exc) for calendar_id, exc in errors.items()
        )
    if last_known is not None:
        last_known.update(fetched)

    events = [event for calendar_id in calendar_ids for event in fetched.get(calendar_id, [])]
    # An invite shows up on each invited calendar under the same event ID;
    # keep one copy so its SMS directives aren't queued (and sent) twice.
    events = list({(event.event_id, event.start): event for event in events}.values())

    logger.info("Fetched %d events across %d calendars", len(events), len(calendar_ids))
    return events


def scan_calendars(
    config: GoogleConfig,
    *,
    lookahead_days: int = 7,
) -> list[tuple[dict, bool, list[CalendarEvent]]]:
    """Fetch upcoming events from every calendar in the user's list, configured or not.

    Returns (calendar, selected by config.calendar_ids, events) for each calendar.
    """
    creds = _authenticate(config)
    service = build("calendar", "v3", credentials=creds)

    now = datetime.now(UTC)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=lookahead_days)).isoformat()

    selected = set(_resolve_calendar_ids(service, config.calendar_ids))
    return [
        (
            calendar,
            calendar["id"] in selected
            or ("primary" in selected and calendar.get("primary", False)),
            _fetch_calendar(service, calendar["id"], time_min, time_max)
            if calendar["accessRole"] in _CALENDAR_SELECTORS["all"]
            else [],
        )
        for calendar in _list_calendars(service)
    ]


def fetch_events_with_retry(
    config: GoogleConfig,
    *,
    lookahead_days: int = 7,
    retries: int = 5,
    retry_delay_seconds: int = 5,
    last_known: dict[str, list[CalendarEvent]] | None = None,
    failed: list[tuple[str, Exception]] | None = None,
) -> list[CalendarEvent]:
    """Fetch events, retrying failures with a doubling delay (5s, 10s, 20s, 40s, 80s).

    Raises the final exception once all attempts are exhausted.
    """
    delay = retry_delay_seconds
    for attempt in range(1, retries + 1):
        try:
            return fetch_events(
                config, lookahead_days=lookahead_days, last_known=last_known, failed=failed
            )
        except Exception as exc:
            logger.warning(
                "Calendar fetch failed (attempt %d/%d), retrying in %.0fs: %s",
                attempt,
                retries + 1,
                delay,
                exc,
            )
            time.sleep(delay)
            delay *= 2
    return fetch_events(
        config, lookahead_days=lookahead_days, last_known=last_known, failed=failed
    )

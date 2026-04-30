"""Google Calendar API client for fetching events."""

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from pushbullet_calend.config import GoogleConfig

_SCOPES = ["https://www.googleapis.com/auth/calendar.readonly"]
logger = logging.getLogger(__name__)


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


def fetch_events(
    config: GoogleConfig,
    *,
    lookahead_days: int = 7,
) -> list[CalendarEvent]:
    """Fetch upcoming events from all configured calendars."""
    creds = _authenticate(config)
    service = build("calendar", "v3", credentials=creds)

    now = datetime.now(UTC)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=lookahead_days)).isoformat()

    events: list[CalendarEvent] = []
    for calendar_id in config.calendar_ids:
        logger.info("Fetching events from calendar %s", calendar_id)
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
                start_raw = item["start"].get("dateTime") or item["start"].get("date")
                start = datetime.fromisoformat(start_raw)
                events.append(
                    CalendarEvent(
                        event_id=item["id"],
                        summary=item.get("summary", ""),
                        description=item.get("description", ""),
                        start=start,
                    )
                )
            page_token = result.get("nextPageToken")
            if not page_token:
                break

    logger.info("Fetched %d events across %d calendars", len(events), len(config.calendar_ids))
    return events

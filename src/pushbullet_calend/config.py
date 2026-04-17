"""Load application configuration from TOML."""

import logging
from dataclasses import dataclass, field
from pathlib import Path

import tomllib

from pushbullet_calend.crypto import decrypt

logger = logging.getLogger(__name__)

_DEFAULT_CONFIG_PATH = Path("config.toml")


@dataclass
class GoogleConfig:
    calendar_ids: list[str] = field(default_factory=lambda: ["primary"])
    credentials_file: str = "credentials.json"
    token_file: str = "token.json"


@dataclass
class PushbulletConfig:
    api_key: str = ""
    device_iden: str = ""


@dataclass
class ScheduleConfig:
    lookahead_days: int = 7
    poll_interval_minutes: int = 5


@dataclass
class DatabaseConfig:
    path: str = "sent_messages.db"


@dataclass
class EmailWatchRule:
    subject: str = ""
    phone_number: str = ""
    message: str = ""


@dataclass
class EmailWatchConfig:
    enabled: bool = False
    imap_server: str = ""
    email_address: str = ""
    app_password: str = ""
    rules: list[EmailWatchRule] = field(default_factory=list)


@dataclass
class AppConfig:
    google: GoogleConfig = field(default_factory=GoogleConfig)
    pushbullet: PushbulletConfig = field(default_factory=PushbulletConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    email_watch: EmailWatchConfig = field(default_factory=EmailWatchConfig)


def load_config(path: Path = _DEFAULT_CONFIG_PATH) -> AppConfig:
    """Load config from a TOML file, falling back to defaults for missing keys."""
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    email_data = data.get("email_watch", {})
    rules = [EmailWatchRule(**r) for r in email_data.pop("rules", [])]
    email_watch = EmailWatchConfig(**email_data, rules=rules)
    if email_watch.enabled and email_watch.app_password:
        try:
            email_watch.app_password = decrypt(email_watch.app_password)
        except Exception:
            logger.error(
                "Failed to decrypt email app_password. "
                "Run 'uv run pushbullet-calend --encrypt-password' to set it up."
            )
            email_watch.enabled = False
    return AppConfig(
        google=GoogleConfig(**data.get("google", {})),
        pushbullet=PushbulletConfig(**data.get("pushbullet", {})),
        schedule=ScheduleConfig(**data.get("schedule", {})),
        database=DatabaseConfig(**data.get("database", {})),
        email_watch=email_watch,
    )

"""Load application configuration from TOML."""

from dataclasses import dataclass, field
from pathlib import Path

import tomllib

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
class AppConfig:
    google: GoogleConfig = field(default_factory=GoogleConfig)
    pushbullet: PushbulletConfig = field(default_factory=PushbulletConfig)
    schedule: ScheduleConfig = field(default_factory=ScheduleConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)


def load_config(path: Path = _DEFAULT_CONFIG_PATH) -> AppConfig:
    """Load config from a TOML file, falling back to defaults for missing keys."""
    data = tomllib.loads(path.read_text()) if path.exists() else {}
    return AppConfig(
        google=GoogleConfig(**data.get("google", {})),
        pushbullet=PushbulletConfig(**data.get("pushbullet", {})),
        schedule=ScheduleConfig(**data.get("schedule", {})),
        database=DatabaseConfig(**data.get("database", {})),
    )

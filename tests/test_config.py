"""Tests for pushbullet_calend.config."""

from unittest.mock import MagicMock

import pytest

from pushbullet_calend import config
from pushbullet_calend.config import (
    AppConfig,
    EmailWatchConfig,
    EmailWatchRule,
    GoogleConfig,
    ScheduleConfig,
    load_config,
)


def test_missing_file_gives_defaults(tmp_path):
    assert load_config(tmp_path / "missing.toml") == AppConfig()


@pytest.mark.parametrize(
    "toml, expected",
    [
        ("", AppConfig()),
        (
            '[google]\ncalendar_ids = ["work", "home"]\n\n[schedule]\npoll_interval_minutes = 1\n',
            AppConfig(
                google=GoogleConfig(calendar_ids=["work", "home"]),
                schedule=ScheduleConfig(poll_interval_minutes=1),
            ),
        ),
        (
            '[[email_watch.rules]]\nsubject = "Free product time… go!"\nphone_number = "555"\n\n'
            '[[email_watch.rules]]\nsender = "alerts@example.com"\nphone_number = "555"\n',
            AppConfig(
                email_watch=EmailWatchConfig(
                    rules=[
                        EmailWatchRule(subject="Free product time… go!", phone_number="555"),
                        EmailWatchRule(sender="alerts@example.com", phone_number="555"),
                    ]
                )
            ),
        ),
    ],
    ids=["empty_file", "partial_file_keeps_other_defaults", "email_rules"],
)
def test_load_config(tmp_path, toml, expected):
    path = tmp_path / "config.toml"
    path.write_text(toml, encoding="utf-8")

    assert load_config(path) == expected


@pytest.mark.parametrize(
    "enabled, decrypt_result, expected_enabled, expected_password",
    [
        ("true", "secret", True, "secret"),
        ("true", ValueError("bad token"), False, "TOKEN"),
        ("false", "secret", False, "TOKEN"),
    ],
    ids=["decrypted", "bad_token_disables_watch", "disabled_skips_decrypt"],
)
def test_email_password_decryption(
    monkeypatch, tmp_path, enabled, decrypt_result, expected_enabled, expected_password
):
    monkeypatch.setattr(config, "decrypt", MagicMock(side_effect=[decrypt_result]))
    path = tmp_path / "config.toml"
    path.write_text(f'[email_watch]\nenabled = {enabled}\napp_password = "TOKEN"\n')

    email_watch = load_config(path).email_watch

    assert email_watch.enabled is expected_enabled
    assert email_watch.app_password == expected_password

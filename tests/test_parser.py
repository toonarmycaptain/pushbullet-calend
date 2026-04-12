"""Tests for pushbullet_calend.parser."""

from datetime import timedelta

import pytest

from pushbullet_calend.parser import SmsDirective, parse_directives

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def directive(*, minutes=0, hours=0, days=0, phone, message):
    return SmsDirective(
        offset=timedelta(minutes=minutes, hours=hours, days=days),
        phone_number=phone,
        message=message,
    )


# ---------------------------------------------------------------------------
# Single directive — all time units and phone variants
# ---------------------------------------------------------------------------

_KIDS_MSG = "Hey, reminder to pick up the kids at 3:15!"
_DINNER_MSG = "Dinner at 7, don't forget!"
_FOOTBALL_MSG = "Football practice starts at 5"


@pytest.mark.parametrize(
    "description, expected",
    [
        (
            f"SMS: -30m | +61412345678 | {_KIDS_MSG}",
            [directive(minutes=30, phone="+61412345678", message=_KIDS_MSG)],
        ),
        (
            f"SMS: -1h | +61412345678 | {_DINNER_MSG}",
            [directive(hours=1, phone="+61412345678", message=_DINNER_MSG)],
        ),
        (
            f"SMS: -2d | +61498765432 | {_FOOTBALL_MSG}",
            [directive(days=2, phone="+61498765432", message=_FOOTBALL_MSG)],
        ),
        # Phone number without '+' prefix gets +1 prepended
        (
            "SMS: -15m | 8175551234 | Meeting in 15 minutes",
            [directive(minutes=15, phone="+18175551234", message="Meeting in 15 minutes")],
        ),
    ],
)
def test_single_directive(description, expected):
    assert parse_directives(description) == expected


# ---------------------------------------------------------------------------
# Multiple directives in one description
# ---------------------------------------------------------------------------


def test_multiple_directives():
    description = (
        f"SMS: -30m | +61412345678 | {_KIDS_MSG}\n"
        f"SMS: -1h | +61412345678 | {_DINNER_MSG}\n"
        f"SMS: -2d | +61498765432 | {_FOOTBALL_MSG}"
    )
    result = parse_directives(description)
    assert result == [
        directive(minutes=30, phone="+61412345678", message=_KIDS_MSG),
        directive(hours=1, phone="+61412345678", message=_DINNER_MSG),
        directive(days=2, phone="+61498765432", message=_FOOTBALL_MSG),
    ]


# ---------------------------------------------------------------------------
# Mixed content — non-SMS lines are ignored
# ---------------------------------------------------------------------------


def test_mixed_content():
    description = (
        "Team outing this Friday.\n"
        "SMS: -30m | +61412345678 | Don't forget the outing!\n"
        "Bring your own lunch.\n"
        "SMS: -1d | +61498765432 | Outing is tomorrow!\n"
        "See you there!"
    )
    result = parse_directives(description)
    assert result == [
        directive(minutes=30, phone="+61412345678", message="Don't forget the outing!"),
        directive(days=1, phone="+61498765432", message="Outing is tomorrow!"),
    ]


# ---------------------------------------------------------------------------
# Empty / no-match descriptions
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "description",
    [
        "",
        "Just a regular calendar event with no SMS directives.",
        "Reminder: pick up milk\nCall dentist",
        # Malformed — missing second pipe
        "SMS: -30m | +61412345678",
        # Malformed — no leading dash on offset
        "SMS: 30m | +61412345678 | message",
    ],
)
def test_no_directives(description):
    assert parse_directives(description) == []


# ---------------------------------------------------------------------------
# Whitespace variations around pipes
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "description, expected_message",
    [
        # Extra spaces around pipes
        (
            "SMS: -30m  |  +61412345678  |  Extra spaces everywhere  ",
            "Extra spaces everywhere",
        ),
        # Minimal spacing (no spaces around pipes)
        (
            "SMS: -30m|+61412345678|Tight spacing",
            "Tight spacing",
        ),
        # Padded message with leading/trailing spaces
        (
            "SMS: -5m |  +61400000000 |   Padded message   ",
            "Padded message",
        ),
    ],
)
def test_whitespace_variations(description, expected_message):
    result = parse_directives(description)
    assert len(result) == 1
    assert result[0].message == expected_message


# ---------------------------------------------------------------------------
# Message text containing pipe characters
# ---------------------------------------------------------------------------


def test_message_with_pipe_characters():
    description = "SMS: -10m | +61412345678 | Option A | Option B | pick one"
    result = parse_directives(description)
    assert len(result) == 1
    assert result[0].message == "Option A | Option B | pick one"


# ---------------------------------------------------------------------------
# Offset field correctness
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "description, expected_offset",
    [
        ("SMS: -45m | +61400000000 | msg", timedelta(minutes=45)),
        ("SMS: -3h | +61400000000 | msg", timedelta(hours=3)),
        ("SMS: -7d | +61400000000 | msg", timedelta(days=7)),
        ("SMS: -120m | +61400000000 | msg", timedelta(minutes=120)),
    ],
)
def test_offset_values(description, expected_offset):
    result = parse_directives(description)
    assert len(result) == 1
    assert result[0].offset == expected_offset

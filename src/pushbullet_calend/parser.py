"""Parse SMS directives from Google Calendar event descriptions."""

import re
from dataclasses import dataclass
from datetime import timedelta

_DIRECTIVE_RE = re.compile(
    r"^SMS:\s*-(\d+)([mhd])\s*\|\s*(\+?\d+)\s*\|\s*(.+)$",
    re.MULTILINE,
)

_UNIT_TO_TIMEDELTA: dict[str, str] = {
    "m": "minutes",
    "h": "hours",
    "d": "days",
}


@dataclass
class SmsDirective:
    offset: timedelta
    phone_number: str
    message: str


def parse_directives(description: str) -> list[SmsDirective]:
    """Return all SMS directives found in *description*.

    Lines not matching the SMS directive syntax are silently ignored.
    """
    directives: list[SmsDirective] = []
    for match in _DIRECTIVE_RE.finditer(description):
        amount_str, unit, phone, message = match.groups()
        offset = timedelta(**{_UNIT_TO_TIMEDELTA[unit]: int(amount_str)})
        directives.append(
            SmsDirective(
                offset=offset,
                phone_number=phone,
                message=message.strip(),
            )
        )
    return directives

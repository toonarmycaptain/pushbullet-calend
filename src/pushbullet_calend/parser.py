"""Parse SMS directives from Google Calendar event descriptions."""

import html
import logging
import re
from dataclasses import dataclass
from datetime import timedelta

logger = logging.getLogger(__name__)

_HTML_TAG_RE = re.compile(r"<[^>]+>")

_DIRECTIVE_RE = re.compile(
    r"^SMS:\s*-(\d+)([mhd])\s*\|\s*(\+?[\d\s\-.()+]+)\s*\|\s*(.+)$",
    re.MULTILINE,
)

_UNIT_TO_TIMEDELTA: dict[str, str] = {
    "m": "minutes",
    "h": "hours",
    "d": "days",
}


def _normalize_phone(raw: str) -> str:
    """Strip formatting characters and default to +1 country code."""
    digits_and_plus = re.sub(r"[^\d+]", "", raw)
    if not digits_and_plus.startswith("+"):
        digits_and_plus = f"+1{digits_and_plus}"
    return digits_and_plus


@dataclass
class SmsDirective:
    offset: timedelta
    phone_number: str
    message: str


def _strip_html(text: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", text, flags=re.IGNORECASE)
    text = _HTML_TAG_RE.sub("", text)
    return html.unescape(text)


def parse_directives(description: str) -> list[SmsDirective]:
    """Return all SMS directives found in *description*.

    Lines not matching the SMS directive syntax are silently ignored.
    """
    description = _strip_html(description)
    directives: list[SmsDirective] = []
    for match in _DIRECTIVE_RE.finditer(description):
        amount_str, unit, phone_raw, message = match.groups()
        offset = timedelta(**{_UNIT_TO_TIMEDELTA[unit]: int(amount_str)})
        phone = _normalize_phone(phone_raw)
        logger.debug("Parsed directive: -%s%s to %s", amount_str, unit, phone)
        directives.append(
            SmsDirective(
                offset=offset,
                phone_number=phone,
                message=message.strip(),
            )
        )
    if not directives:
        logger.debug("No SMS directives found in description")
    return directives

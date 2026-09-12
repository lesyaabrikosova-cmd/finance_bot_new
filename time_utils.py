"""Application time helpers.

Amvera containers may run in UTC.  User-facing calendar operations belong to
the Moscow timezone, so they must not depend on the host's local timezone.
"""

from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo


MOSCOW_TIMEZONE = ZoneInfo("Europe/Moscow")


def moscow_now() -> datetime:
    """Return the current timezone-aware Moscow datetime."""

    return datetime.now(MOSCOW_TIMEZONE)


def moscow_today() -> date:
    """Return today's calendar date in Moscow."""

    return moscow_now().date()

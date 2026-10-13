"""Timezone-aware "today" helper.

The configured timezone comes from ``POTLUCK_TIMEZONE`` (IANA name, default
``Asia/Kolkata``). An invalid or unavailable name falls back to the default
with a printed warning; it never raises.
"""

from __future__ import annotations

import datetime
import os
from zoneinfo import ZoneInfo

DEFAULT_TIMEZONE = "Asia/Kolkata"


def get_timezone() -> datetime.tzinfo:
    """Return the configured timezone, falling back to the default."""
    name = (os.getenv("POTLUCK_TIMEZONE") or "").strip() or DEFAULT_TIMEZONE
    try:
        return ZoneInfo(name)
    except Exception:
        print(
            f"warning: invalid POTLUCK_TIMEZONE {name!r}; "
            f"using {DEFAULT_TIMEZONE}",
            flush=True,
        )
        try:
            return ZoneInfo(DEFAULT_TIMEZONE)
        except Exception:
            # No tz database at all: fixed IST offset keeps the app running.
            return datetime.timezone(datetime.timedelta(hours=5, minutes=30))


def today_in_tz(
    now: datetime.datetime | None = None,
    tz: datetime.tzinfo | None = None,
) -> datetime.date:
    """Return today's date in ``tz`` (default: the configured timezone).

    ``now`` may be injected for tests; naive values are treated as UTC.
    """
    zone = tz if tz is not None else get_timezone()
    if now is None:
        now = datetime.datetime.now(datetime.timezone.utc)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=datetime.timezone.utc)
    return now.astimezone(zone).date()

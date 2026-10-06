from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

US_SESSION_TIMEZONE = ZoneInfo("America/New_York")
OPEN_WINDOW = (time(9, 30), time(16, 0))
CLOSE_WINDOW = (time(15, 50), time(16, 0))


def in_window(now: datetime, start: time, end: time, *, timezone_name: str = "America/New_York") -> bool:
    """Return whether now falls inside a daily wall-clock window in the named timezone."""
    local_now = now.astimezone(ZoneInfo(timezone_name))
    if start < end:
        return start <= local_now.time() < end
    return local_now.time() >= start or local_now.time() < end


def is_market_open(now: datetime) -> bool:
    return in_window(now, *OPEN_WINDOW)


def is_close_window(now: datetime) -> bool:
    return in_window(now, *CLOSE_WINDOW)


def next_window_boundary(now: datetime, *, action: str) -> datetime:
    local_now = now.astimezone(US_SESSION_TIMEZONE)
    if action == "open":
        target = datetime.combine(local_now.date(), OPEN_WINDOW[0], tzinfo=US_SESSION_TIMEZONE)
    else:
        target = datetime.combine(local_now.date(), CLOSE_WINDOW[0], tzinfo=US_SESSION_TIMEZONE)
    if target <= local_now:
        target += timedelta(days=1)
    return target

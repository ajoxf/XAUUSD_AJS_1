"""DST-aware session window - spec §3 Gate 2 + Fix L4."""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Tuple

import pytz

NY_TZ = pytz.timezone("America/New_York")
UTC = pytz.utc


def session_window_utc(d: date) -> Tuple[datetime, datetime]:
    """Returns (start_utc, end_utc) for NY open through NY open + 4h."""
    ny_open = NY_TZ.localize(datetime(d.year, d.month, d.day, 9, 30))
    start = ny_open.astimezone(UTC)
    end = start + timedelta(hours=4)
    return start, end


def ny_time_to_utc(d: date, hour: int, minute: int) -> datetime:
    """Convert an NY local clock time to UTC, DST-aware."""
    ny_local = NY_TZ.localize(datetime(d.year, d.month, d.day, hour, minute))
    return ny_local.astimezone(UTC)


def in_session(now_utc: datetime, d: date) -> bool:
    start, end = session_window_utc(d)
    return start <= now_utc <= end


def session_end_utc(d: date) -> datetime:
    """21:00 UTC hard close - final safety net per spec §6."""
    return UTC.localize(datetime(d.year, d.month, d.day, 21, 0))


def force_close_2055_utc(d: date) -> datetime:
    """20:55 UTC partial-close trigger - spec v3.1 §6."""
    return UTC.localize(datetime(d.year, d.month, d.day, 20, 55))


def is_wednesday(d: date) -> bool:
    return d.weekday() == 2

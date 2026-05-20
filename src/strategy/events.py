"""Economic event calendar - spec §3 Gate 3 + Fix L7.

Hardcoded FOMC / NFP / CPI dates 2021-2026. CPI blocks the release day only;
FOMC and NFP block the release day AND the prior US business day.
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Optional, Tuple

FOMC_DATES = {
    # 2021
    date(2021, 1, 27), date(2021, 3, 17), date(2021, 4, 28), date(2021, 6, 16),
    date(2021, 7, 28), date(2021, 9, 22), date(2021, 11, 3), date(2021, 12, 15),
    # 2022
    date(2022, 1, 26), date(2022, 3, 16), date(2022, 5, 4), date(2022, 6, 15),
    date(2022, 7, 27), date(2022, 9, 21), date(2022, 11, 2), date(2022, 12, 14),
    # 2023
    date(2023, 2, 1), date(2023, 3, 22), date(2023, 5, 3), date(2023, 6, 14),
    date(2023, 7, 26), date(2023, 9, 20), date(2023, 11, 1), date(2023, 12, 13),
    # 2024
    date(2024, 1, 31), date(2024, 3, 20), date(2024, 5, 1), date(2024, 6, 12),
    date(2024, 7, 31), date(2024, 9, 18), date(2024, 11, 7), date(2024, 12, 18),
    # 2025
    date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7), date(2025, 6, 18),
    date(2025, 7, 30), date(2025, 9, 17), date(2025, 10, 29), date(2025, 12, 10),
    # 2026
    date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29), date(2026, 6, 17),
    date(2026, 7, 29), date(2026, 9, 16), date(2026, 10, 28), date(2026, 12, 9),
}

NFP_DATES = {
    # First Friday of each month, 2021-2026 (verified release dates).
    date(2021, 1, 8), date(2021, 2, 5), date(2021, 3, 5), date(2021, 4, 2),
    date(2021, 5, 7), date(2021, 6, 4), date(2021, 7, 2), date(2021, 8, 6),
    date(2021, 9, 3), date(2021, 10, 8), date(2021, 11, 5), date(2021, 12, 3),
    date(2022, 1, 7), date(2022, 2, 4), date(2022, 3, 4), date(2022, 4, 1),
    date(2022, 5, 6), date(2022, 6, 3), date(2022, 7, 8), date(2022, 8, 5),
    date(2022, 9, 2), date(2022, 10, 7), date(2022, 11, 4), date(2022, 12, 2),
    date(2023, 1, 6), date(2023, 2, 3), date(2023, 3, 10), date(2023, 4, 7),
    date(2023, 5, 5), date(2023, 6, 2), date(2023, 7, 7), date(2023, 8, 4),
    date(2023, 9, 1), date(2023, 10, 6), date(2023, 11, 3), date(2023, 12, 8),
    date(2024, 1, 5), date(2024, 2, 2), date(2024, 3, 8), date(2024, 4, 5),
    date(2024, 5, 3), date(2024, 6, 7), date(2024, 7, 5), date(2024, 8, 2),
    date(2024, 9, 6), date(2024, 10, 4), date(2024, 11, 1), date(2024, 12, 6),
    date(2025, 1, 10), date(2025, 2, 7), date(2025, 3, 7), date(2025, 4, 4),
    date(2025, 5, 2), date(2025, 6, 6), date(2025, 7, 3), date(2025, 8, 1),
    date(2025, 9, 5), date(2025, 10, 3), date(2025, 11, 7), date(2025, 12, 5),
    date(2026, 1, 9), date(2026, 2, 6), date(2026, 3, 6), date(2026, 4, 3),
    date(2026, 5, 1), date(2026, 6, 5), date(2026, 7, 2), date(2026, 8, 7),
    date(2026, 9, 4), date(2026, 10, 2), date(2026, 11, 6), date(2026, 12, 4),
}

CPI_DATES = {
    date(2021, 1, 13), date(2021, 2, 10), date(2021, 3, 10), date(2021, 4, 13),
    date(2021, 5, 12), date(2021, 6, 10), date(2021, 7, 13), date(2021, 8, 11),
    date(2021, 9, 14), date(2021, 10, 13), date(2021, 11, 10), date(2021, 12, 10),
    date(2022, 1, 12), date(2022, 2, 10), date(2022, 3, 10), date(2022, 4, 12),
    date(2022, 5, 11), date(2022, 6, 10), date(2022, 7, 13), date(2022, 8, 10),
    date(2022, 9, 13), date(2022, 10, 13), date(2022, 11, 10), date(2022, 12, 13),
    date(2023, 1, 12), date(2023, 2, 14), date(2023, 3, 14), date(2023, 4, 12),
    date(2023, 5, 10), date(2023, 6, 13), date(2023, 7, 12), date(2023, 8, 10),
    date(2023, 9, 13), date(2023, 10, 12), date(2023, 11, 14), date(2023, 12, 12),
    date(2024, 1, 11), date(2024, 2, 13), date(2024, 3, 12), date(2024, 4, 10),
    date(2024, 5, 15), date(2024, 6, 12), date(2024, 7, 11), date(2024, 8, 14),
    date(2024, 9, 11), date(2024, 10, 10), date(2024, 11, 13), date(2024, 12, 11),
    date(2025, 1, 15), date(2025, 2, 12), date(2025, 3, 12), date(2025, 4, 10),
    date(2025, 5, 13), date(2025, 6, 11), date(2025, 7, 15), date(2025, 8, 12),
    date(2025, 9, 11), date(2025, 10, 15), date(2025, 11, 13), date(2025, 12, 10),
    date(2026, 1, 14), date(2026, 2, 11), date(2026, 3, 11), date(2026, 4, 14),
    date(2026, 5, 13), date(2026, 6, 10), date(2026, 7, 14), date(2026, 8, 12),
    date(2026, 9, 10), date(2026, 10, 14), date(2026, 11, 12), date(2026, 12, 10),
}


def _is_business_day(d: date) -> bool:
    # weekday(): Mon=0 .. Sun=6
    return d.weekday() < 5


def _next_business_day(d: date) -> date:
    n = d + timedelta(days=1)
    while not _is_business_day(n):
        n += timedelta(days=1)
    return n


def event_on(d: date) -> Optional[str]:
    if d in FOMC_DATES:
        return "FOMC"
    if d in NFP_DATES:
        return "NFP"
    if d in CPI_DATES:
        return "CPI"
    return None


def is_blocked_day(today: date) -> Tuple[bool, Optional[str]]:
    """Returns (blocked, reason). Blocks today if event day; blocks today if
    tomorrow is FOMC or NFP (NOT CPI per spec §3 Gate 3)."""
    today_event = event_on(today)
    if today_event is not None:
        return True, f"Event day: {today_event}"
    tomorrow = _next_business_day(today)
    tomorrow_event = event_on(tomorrow)
    if tomorrow_event in ("FOMC", "NFP"):
        return True, f"Pre-event day before {tomorrow_event}"
    return False, None

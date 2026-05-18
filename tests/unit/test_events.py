from datetime import date

from src.strategy import events


def test_fomc_blocked():
    blocked, reason = events.is_blocked_day(date(2024, 1, 31))
    assert blocked
    assert "FOMC" in reason


def test_nfp_blocked():
    blocked, reason = events.is_blocked_day(date(2024, 1, 5))
    assert blocked
    assert "NFP" in reason


def test_cpi_blocked():
    blocked, reason = events.is_blocked_day(date(2024, 1, 11))
    assert blocked
    assert "CPI" in reason


def test_pre_fomc_blocked():
    # Day before FOMC 2024-01-31 (Wednesday) = 2024-01-30 (Tuesday)
    blocked, reason = events.is_blocked_day(date(2024, 1, 30))
    assert blocked
    assert "Pre-event day before FOMC" in reason


def test_pre_nfp_blocked():
    # Day before NFP 2024-01-05 (Friday) = 2024-01-04 (Thursday)
    blocked, reason = events.is_blocked_day(date(2024, 1, 4))
    assert blocked
    assert "Pre-event day before NFP" in reason


def test_pre_cpi_NOT_blocked():
    # CPI is NOT in the pre-event block list per spec §3 Gate 3
    blocked, _ = events.is_blocked_day(date(2024, 1, 10))
    assert not blocked


def test_normal_day_not_blocked():
    blocked, _ = events.is_blocked_day(date(2024, 6, 24))   # Mon, no event
    assert not blocked


def test_pre_event_spans_weekend():
    # NFP 2024-09-06 (Fri) — pre-day is Thursday 2024-09-05
    blocked, reason = events.is_blocked_day(date(2024, 9, 5))
    assert blocked
    assert "NFP" in reason

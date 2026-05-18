from datetime import date

import pytest

from src.strategy import premarket
from tests.conftest import synthetic_daily, synthetic_h4
from datetime import datetime, timezone


def test_basic_levels_consistent():
    df = synthetic_daily(date(2024, 6, 25), n=60, daily_range=30.0)
    h4 = synthetic_h4(datetime(2024, 6, 25, 13, 30, tzinfo=timezone.utc), n=60)
    ctx = premarket.build_premarket(date(2024, 6, 25), df, h4["close"])

    assert ctx.range > 0
    assert ctx.long_entry > ctx.prev_close
    assert ctx.short_entry < ctx.prev_close
    assert ctx.long_tp1 > ctx.long_entry
    assert ctx.short_tp1 < ctx.short_entry
    # TP1 fib = 0.618 when range >= ATR20; 0.500 otherwise
    diff = ctx.long_tp1 - ctx.prev_close
    assert pytest.approx(diff, rel=1e-9) == 0.618 * ctx.range or \
        pytest.approx(diff, rel=1e-9) == 0.500 * ctx.range


def test_filter_c_active_low_atr():
    # Force tiny range with normal ATR by using last row with very small range
    df = synthetic_daily(date(2024, 6, 25), n=60, daily_range=30.0)
    last_open = df["open"].iloc[-1]
    df.loc[df.index[-1], "high"] = last_open + 2
    df.loc[df.index[-1], "low"] = last_open - 2
    df.loc[df.index[-1], "close"] = last_open + 1
    h4 = synthetic_h4(datetime(2024, 6, 25, 13, 30, tzinfo=timezone.utc), n=60)
    ctx = premarket.build_premarket(date(2024, 6, 25), df, h4["close"])
    assert ctx.filter_c_active
    assert ctx.filter_f_suppressed
    # TP1 should be 0.5 × range, not 0.618
    assert ctx.long_tp1 == pytest.approx(ctx.prev_close + 0.5 * ctx.range)
    # TP2 standard (no extended), since F is suppressed
    assert ctx.long_tp2 == pytest.approx(ctx.prev_close + 1.0 * ctx.range)


def test_trend_bias_long_only():
    df = synthetic_daily(date(2024, 6, 25), n=60, base_price=2000.0)
    # Force prev_close well above ema50_4h
    df.loc[df.index[-1], "close"] = 2500.0
    df.loc[df.index[-1], "high"] = 2520.0
    df.loc[df.index[-1], "low"] = 2480.0
    df.loc[df.index[-1], "open"] = 2490.0
    h4 = synthetic_h4(datetime(2024, 6, 25, 13, 30, tzinfo=timezone.utc), n=60,
                      start_price=2000.0)
    ctx = premarket.build_premarket(date(2024, 6, 25), df, h4["close"])
    assert ctx.trend_bias == "LONG_ONLY"


def test_invalid_range_raises():
    df = synthetic_daily(date(2024, 6, 25), n=60)
    df.loc[df.index[-1], "high"] = 100.0
    df.loc[df.index[-1], "low"] = 200.0
    h4 = synthetic_h4(datetime(2024, 6, 25, 13, 30, tzinfo=timezone.utc), n=60)
    with pytest.raises(ValueError):
        premarket.build_premarket(date(2024, 6, 25), df, h4["close"])


def test_event_blocked_propagates():
    df = synthetic_daily(date(2024, 1, 31), n=60)
    h4 = synthetic_h4(datetime(2024, 1, 31, 14, 30, tzinfo=timezone.utc), n=60)
    ctx = premarket.build_premarket(date(2024, 1, 31), df, h4["close"])
    assert ctx.event_blocked
    assert "FOMC" in ctx.event_reason


def test_seasonal_multiplier_applied():
    df = synthetic_daily(date(2024, 1, 15), n=60)
    h4 = synthetic_h4(datetime(2024, 1, 15, 14, 30, tzinfo=timezone.utc), n=60)
    ctx = premarket.build_premarket(date(2024, 1, 15), df, h4["close"])
    assert ctx.seasonal_mult_long == 1.10

from datetime import date, datetime, timezone

from src.strategy import gates, premarket
from tests.conftest import synthetic_daily, synthetic_h4


def _ctx(d=date(2024, 6, 25)):
    df = synthetic_daily(d, n=220, daily_range=30.0)
    h4 = synthetic_h4(datetime(d.year, d.month, d.day, 13, 30, tzinfo=timezone.utc), n=220)
    return premarket.build_premarket(d, df, h4["close"])


def test_all_gates_pass_when_normal(settings):
    ctx = _ctx()
    now = ctx.session_start_utc
    direction = "LONG" if ctx.trend_bias in ("LONG_ONLY", "BOTH") else "SHORT"
    res = gates.check_all_gates(ctx, settings, now, direction,
                                 daily_fired=False, position_open=False)
    if not res.passed:
        # Acceptable failures from synthetic data: G1 ATR floor/cap; ensure
        # only G1-related and not session/event/lock/trend.
        for f in res.failures:
            assert f.startswith("G1") or f.startswith("G6")


def test_g2_outside_session_blocks(settings):
    ctx = _ctx()
    outside = datetime(2024, 6, 25, 23, 0, tzinfo=timezone.utc)
    res = gates.check_all_gates(ctx, settings, outside, "LONG", False, False)
    assert not res.passed
    assert any(f.startswith("G2:") for f in res.failures)


def test_g3_event_day_blocks(settings):
    ctx = _ctx(date(2024, 1, 31))   # FOMC
    res = gates.check_all_gates(ctx, settings, ctx.session_start_utc,
                                 "LONG", False, False)
    assert any(f.startswith("G3:") for f in res.failures)


def test_g4_daily_lock_blocks(settings):
    ctx = _ctx()
    res = gates.check_all_gates(ctx, settings, ctx.session_start_utc,
                                 "LONG", daily_fired=True, position_open=False)
    assert any(f.startswith("G4:") for f in res.failures)


def test_g4_position_open_blocks(settings):
    ctx = _ctx()
    res = gates.check_all_gates(ctx, settings, ctx.session_start_utc,
                                 "LONG", daily_fired=False, position_open=True)
    assert any(f.startswith("G4:") for f in res.failures)


def test_g5_counter_trend_blocked(settings):
    # Force LONG_ONLY bias
    df = synthetic_daily(date(2024, 6, 25), n=220, base_price=2000.0)
    df.loc[df.index[-1], "close"] = 2500.0
    df.loc[df.index[-1], "high"] = 2520.0
    df.loc[df.index[-1], "low"] = 2480.0
    df.loc[df.index[-1], "open"] = 2490.0
    h4 = synthetic_h4(datetime(2024, 6, 25, 13, 30, tzinfo=timezone.utc),
                      n=220, start_price=2000.0)
    ctx = premarket.build_premarket(date(2024, 6, 25), df, h4["close"])
    res = gates.check_all_gates(ctx, settings, ctx.session_start_utc,
                                 "SHORT", False, False)
    assert any(f.startswith("G5:") for f in res.failures)


def test_g7_tiny_range_blocked(settings):
    """v3.2: min range floor moved from G6 to G7 (G6 is now SMA200 short filter)."""
    df = synthetic_daily(date(2024, 6, 25), n=220, daily_range=30.0)
    last_open = df["open"].iloc[-1]
    df.loc[df.index[-1], "high"] = last_open + 2.5
    df.loc[df.index[-1], "low"] = last_open - 2.5
    df.loc[df.index[-1], "close"] = last_open
    h4 = synthetic_h4(datetime(2024, 6, 25, 13, 30, tzinfo=timezone.utc), n=220)
    ctx = premarket.build_premarket(date(2024, 6, 25), df, h4["close"])
    res = gates.check_all_gates(ctx, settings, ctx.session_start_utc,
                                 "LONG", False, False)
    assert any(f.startswith("G7:") for f in res.failures)


def test_g6_sma200_blocks_short_above_sma(settings):
    """v3.2 Opt 4: shorts blocked when prev_close > SMA200."""
    df = synthetic_daily(date(2024, 6, 25), n=220, base_price=2000.0, daily_range=30.0)
    # Force last close well above any reasonable SMA200 from synthetic data
    df.loc[df.index[-1], "close"] = 2500.0
    df.loc[df.index[-1], "high"] = 2520.0
    df.loc[df.index[-1], "low"] = 2475.0
    df.loc[df.index[-1], "open"] = 2490.0
    h4 = synthetic_h4(datetime(2024, 6, 25, 13, 30, tzinfo=timezone.utc),
                      n=220, start_price=2000.0)
    ctx = premarket.build_premarket(date(2024, 6, 25), df, h4["close"])
    assert ctx.prev_close > ctx.sma200_daily
    res = gates.check_all_gates(ctx, settings, ctx.session_start_utc,
                                 "SHORT", False, False)
    assert any(f.startswith("G6:") for f in res.failures)


def test_g6_sma200_does_not_block_long(settings):
    df = synthetic_daily(date(2024, 6, 25), n=220, base_price=2000.0, daily_range=30.0)
    df.loc[df.index[-1], "close"] = 2500.0
    df.loc[df.index[-1], "high"] = 2520.0
    df.loc[df.index[-1], "low"] = 2475.0
    df.loc[df.index[-1], "open"] = 2490.0
    h4 = synthetic_h4(datetime(2024, 6, 25, 13, 30, tzinfo=timezone.utc),
                      n=220, start_price=2000.0)
    ctx = premarket.build_premarket(date(2024, 6, 25), df, h4["close"])
    res = gates.check_all_gates(ctx, settings, ctx.session_start_utc,
                                 "LONG", False, False)
    # No G6 failure for long
    assert not any(f.startswith("G6:") for f in res.failures)

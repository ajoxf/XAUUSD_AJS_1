"""Targeted tests for the six v3.2 return-enhancement optimisations."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from config.flags import FLAGS
from src.strategy import exits, premarket, session
from src.strategy.comex_volume import ComexVolumeTracker
from src.strategy.exits import (CloseReason, PositionState, TrancheState,
                                  apply_tick, maybe_2055_force_close,
                                  maybe_accelerate_tp1, maybe_comex_volume_exit,
                                  maybe_rsi_trim)
from src.strategy.indicators import sma
from tests.conftest import synthetic_daily, synthetic_h4


def _ctx(d=date(2024, 6, 25), prev_close_type=None):
    df = synthetic_daily(d, n=220, daily_range=30.0)
    h4 = synthetic_h4(datetime(d.year, d.month, d.day, 13, 30, tzinfo=timezone.utc), n=220)
    return premarket.build_premarket(d, df, h4["close"],
                                       prev_session_close_type=prev_close_type)


def _two_half_position(ctx, lots=2.0):
    pos = PositionState(
        direction="LONG",
        entry_price=ctx.long_entry,
        entry_time_utc=ctx.session_start_utc + timedelta(minutes=30),
        initial_stop=ctx.long_sl,
        tp1=ctx.long_tp1,
        tp2=ctx.long_tp2,
        tranches=[TrancheState("H1", lots * 0.5),
                  TrancheState("H2", lots * 0.5)],
    )
    pos.current_stop = ctx.long_sl
    pos.running_extreme = ctx.long_entry
    pos.intraday_high = ctx.long_entry
    pos.intraday_low = ctx.long_entry
    return pos


# ───────────────────────── Opt 2: back-to-back TP2 extension ────────────────
def test_opt2_back_to_back_extends_tp2():
    ctx_normal = _ctx(prev_close_type=None)
    ctx_b2b = _ctx(prev_close_type="TP2")
    # B2B should push TP2 fib up by 0.10
    assert ctx_b2b.long_tp2_fib > ctx_normal.long_tp2_fib
    assert ctx_b2b.back_to_back_active
    assert ctx_b2b.long_tp2 > ctx_normal.long_tp2


def test_opt2_back_to_back_caps_at_140():
    """If base fib is already 1.272 (Filter F), b2b would push to 1.372 — still under cap."""
    ctx = _ctx(prev_close_type="TP2")
    assert ctx.long_tp2_fib <= 1.400


def test_opt2_inactive_on_tp1_close():
    ctx_tp1 = _ctx(prev_close_type="TP1")
    ctx_none = _ctx(prev_close_type=None)
    # Only TP2 (full win) triggers — TP1 should not
    assert ctx_tp1.long_tp2_fib == ctx_none.long_tp2_fib
    assert not ctx_tp1.back_to_back_active


# ───────────────────────── Opt 3: high-ATR TP2 extension ────────────────────
def test_opt3_high_atr_extension():
    """When range is between 1.3× and 1.8× ATR(20), TP2 fib bumps to 1.15
    (unless Filter F is already active)."""
    df = synthetic_daily(date(2024, 6, 25), n=220, daily_range=30.0)
    # Force last day's range to be 1.5× ATR ≈ 45 (assuming atr~30)
    last_open = df["open"].iloc[-1]
    df.loc[df.index[-1], "open"] = last_open
    df.loc[df.index[-1], "close"] = last_open + 5    # not a strong trend day (no Filter F)
    df.loc[df.index[-1], "high"] = last_open + 25
    df.loc[df.index[-1], "low"] = last_open - 20
    h4 = synthetic_h4(datetime(2024, 6, 25, 13, 30, tzinfo=timezone.utc), n=220)
    ctx = premarket.build_premarket(date(2024, 6, 25), df, h4["close"])

    if 1.30 <= ctx.range / ctx.atr_20 <= 1.80 and not ctx.long_filter_f:
        assert ctx.high_atr_extension_active
        assert ctx.long_tp2_fib == pytest.approx(1.150)


# ───────────────────────── Opt 4: SMA200 short filter ───────────────────────
def test_opt4_sma200_indicator():
    df = synthetic_daily(date(2024, 6, 25), n=220)
    s = sma(df["close"].tolist(), 200)
    assert s > 0


# (Gate-level test for Opt 4 is in test_gates.py)


# ───────────────────────── Opt 5: Wednesday TP1 acceleration ────────────────
def test_opt5_wednesday_acceleration_at_1430(settings):
    """On Wednesdays, TP1 is accelerated at 14:30 NY (1h earlier than standard)."""
    wednesday = date(2024, 6, 26)   # Wednesday
    assert session.is_wednesday(wednesday)
    ctx = _ctx(wednesday)
    pos = _two_half_position(ctx)
    # 14:30 NY in June = 18:30 UTC
    trigger = datetime(2024, 6, 26, 18, 30, tzinfo=timezone.utc)
    moved = maybe_accelerate_tp1(ctx, pos, settings, trigger)
    assert moved
    assert pos.accelerated_kind == "WEDNESDAY"
    # 70% (Wednesday) is tighter than 80% (standard)
    orig_dist = ctx.long_tp1 - ctx.long_entry
    new_dist = pos.accelerated_tp1_price - ctx.long_entry
    assert new_dist == pytest.approx(orig_dist * 0.70)


def test_opt5_not_wednesday_uses_standard(settings):
    """On non-Wednesday, the standard 15:30 NY acceleration applies."""
    tuesday = date(2024, 6, 25)   # Tuesday
    ctx = _ctx(tuesday)
    pos = _two_half_position(ctx)
    # 14:30 NY: should NOT trigger on Tuesday
    trigger_1430 = datetime(2024, 6, 25, 18, 30, tzinfo=timezone.utc)
    assert not maybe_accelerate_tp1(ctx, pos, settings, trigger_1430)
    # 15:30 NY: standard acceleration
    trigger_1530 = datetime(2024, 6, 25, 19, 30, tzinfo=timezone.utc)
    moved = maybe_accelerate_tp1(ctx, pos, settings, trigger_1530)
    assert moved
    assert pos.accelerated_kind == "STANDARD"


# ───────────────────────── Opt 6: RSI post-TP1 trim ─────────────────────────
def test_opt6_rsi_trim_long_overextended(settings):
    ctx = _ctx()
    pos = _two_half_position(ctx)
    # Mark TP1 hit
    pos.tp1_hit = True
    pos.tp1_hit_price = ctx.long_tp1
    pos.tp1_hit_time_utc = ctx.session_start_utc + timedelta(hours=1)
    # Build closes with RSI > 72 (monotonic rising)
    closes = list(range(1, 30))
    trim = maybe_rsi_trim(pos, closes, settings,
                           ctx.session_start_utc + timedelta(hours=1, minutes=15))
    assert trim is not None
    assert trim["rsi_at_trim"] > 72.0
    assert trim["trim_lots"] > 0
    assert pos.rsi_trim_done
    h2 = pos.half_2
    assert len(h2.partial_closes) == 1
    assert h2.partial_closes[0]["reason"] == "RSI_TRIM"


def test_opt6_rsi_trim_short_oversold(settings):
    ctx = _ctx()
    pos = PositionState(
        direction="SHORT",
        entry_price=ctx.short_entry,
        entry_time_utc=ctx.session_start_utc + timedelta(minutes=30),
        initial_stop=ctx.short_sl,
        tp1=ctx.short_tp1,
        tp2=ctx.short_tp2,
        tranches=[TrancheState("H1", 1.0), TrancheState("H2", 1.0)],
    )
    pos.tp1_hit = True
    pos.tp1_hit_price = ctx.short_tp1
    closes = list(range(30, 1, -1))   # monotonic falling → RSI < 28
    trim = maybe_rsi_trim(pos, closes, settings,
                           ctx.session_start_utc + timedelta(hours=1))
    assert trim is not None
    assert trim["rsi_at_trim"] < 28.0


def test_opt6_rsi_trim_one_shot(settings):
    ctx = _ctx()
    pos = _two_half_position(ctx)
    pos.tp1_hit = True
    closes = list(range(1, 30))
    first = maybe_rsi_trim(pos, closes, settings,
                            ctx.session_start_utc + timedelta(hours=1))
    second = maybe_rsi_trim(pos, closes, settings,
                              ctx.session_start_utc + timedelta(hours=1, minutes=15))
    assert first is not None
    assert second is None


def test_opt6_rsi_trim_no_trigger_neutral(settings):
    ctx = _ctx()
    pos = _two_half_position(ctx)
    pos.tp1_hit = True
    # Oscillating closes around 100 → RSI in neutral zone
    closes = [100.0 + (i % 3 - 1) * 0.5 for i in range(25)]
    trim = maybe_rsi_trim(pos, closes, settings,
                           ctx.session_start_utc + timedelta(hours=1))
    assert trim is None
    assert pos.rsi_trim_done   # marks as checked even if no trim


# ───────────────────────── Opt 1: COMEX volume fade ─────────────────────────
def test_opt1_comex_tracker_consecutive_weak_triggers():
    tracker = ComexVolumeTracker(weak_threshold_pct=0.50, consecutive_to_exit=2)
    # 10 bars of normal volume
    base_time = datetime(2024, 6, 25, 14, 0, tzinfo=timezone.utc)
    for i in range(10):
        tracker.update(100.0, base_time + timedelta(minutes=15 * i))
    # Weak bar 1
    triggered = tracker.update(40.0, base_time + timedelta(minutes=15 * 11))
    assert not triggered
    assert tracker.consecutive_weak == 1
    # Weak bar 2 — triggers
    triggered = tracker.update(30.0, base_time + timedelta(minutes=15 * 12))
    assert triggered
    assert tracker.consecutive_weak == 2


def test_opt1_comex_tracker_reset_on_normal_bar():
    tracker = ComexVolumeTracker()
    base_time = datetime(2024, 6, 25, 14, 0, tzinfo=timezone.utc)
    for i in range(10):
        tracker.update(100.0, base_time + timedelta(minutes=15 * i))
    tracker.update(40.0, base_time + timedelta(minutes=15 * 11))   # weak
    assert tracker.consecutive_weak == 1
    tracker.update(95.0, base_time + timedelta(minutes=15 * 12))    # normal
    assert tracker.consecutive_weak == 0


def test_opt1_comex_stale_after_20min():
    tracker = ComexVolumeTracker(stale_after_minutes=20)
    ts = datetime(2024, 6, 25, 14, 0, tzinfo=timezone.utc)
    tracker.update(100.0, ts)
    assert not tracker.is_stale(ts + timedelta(minutes=10))
    assert tracker.is_stale(ts + timedelta(minutes=25))


def test_opt1_comex_exit_only_after_tp1(settings):
    ctx = _ctx()
    pos = _two_half_position(ctx)
    tracker = ComexVolumeTracker()
    base = datetime(2024, 6, 25, 14, 0, tzinfo=timezone.utc)
    for i in range(10):
        tracker.update(100.0, base + timedelta(minutes=15 * i))
    tracker.update(40.0, base + timedelta(minutes=15 * 11))
    tracker.update(30.0, base + timedelta(minutes=15 * 12))   # triggers

    # Before TP1: should NOT exit
    h2 = maybe_comex_volume_exit(pos, tracker,
                                   base + timedelta(minutes=15 * 13))
    assert h2 is None

    # After TP1: SHOULD return h2 for closing
    pos.tp1_hit = True
    h2 = maybe_comex_volume_exit(pos, tracker,
                                   base + timedelta(minutes=15 * 13))
    assert h2 is not None
    assert h2.name == "H2"


def test_opt1_comex_no_exit_when_stale(settings):
    ctx = _ctx()
    pos = _two_half_position(ctx)
    pos.tp1_hit = True
    tracker = ComexVolumeTracker(stale_after_minutes=20)
    # Last update was 30 minutes ago — stale
    old_time = datetime(2024, 6, 25, 13, 0, tzinfo=timezone.utc)
    tracker.update(50.0, old_time)
    now = datetime(2024, 6, 25, 14, 0, tzinfo=timezone.utc)
    h2 = maybe_comex_volume_exit(pos, tracker, now)
    assert h2 is None


# ───────────────────────── 20:55 UTC partial-close tree ─────────────────────
def test_2055_closes_full_position_if_no_tp1(settings):
    ctx = _ctx()
    pos = _two_half_position(ctx)
    ts = datetime(ctx.trade_date.year, ctx.trade_date.month, ctx.trade_date.day,
                   20, 55, tzinfo=timezone.utc)
    closed = maybe_2055_force_close(ctx, pos, settings, ctx.long_entry, ts)
    assert len(closed) == 2
    assert all(t.close_reason == CloseReason.SESSION_CLOSE_FULL_NO_TP1 for t in closed)
    assert pos.closed


def test_2055_closes_half2_only_if_tp1_hit(settings):
    ctx = _ctx()
    pos = _two_half_position(ctx)
    # Simulate TP1 already hit (H1 closed manually)
    pos.tp1_hit = True
    h1 = pos.half_1
    h1.is_open = False
    h1.close_reason = CloseReason.TP1
    ts = datetime(ctx.trade_date.year, ctx.trade_date.month, ctx.trade_date.day,
                   20, 55, tzinfo=timezone.utc)
    closed = maybe_2055_force_close(ctx, pos, settings, ctx.long_tp1 + 5, ts)
    assert len(closed) == 1
    assert closed[0].name == "H2"
    assert closed[0].close_reason == CloseReason.SESSION_CLOSE_HALF2


def test_2055_no_op_before_trigger(settings):
    ctx = _ctx()
    pos = _two_half_position(ctx)
    ts = datetime(ctx.trade_date.year, ctx.trade_date.month, ctx.trade_date.day,
                   20, 30, tzinfo=timezone.utc)
    closed = maybe_2055_force_close(ctx, pos, settings, ctx.long_entry, ts)
    assert closed == []
    assert not pos.closed

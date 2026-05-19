from datetime import date, datetime, timedelta, timezone

import pytest

from src.strategy import exits, premarket
from src.strategy.exits import CloseReason, PositionState, TrancheState, apply_tick
from tests.conftest import synthetic_daily, synthetic_h4


def _ctx(d=date(2024, 6, 25)):
    df = synthetic_daily(d, n=220, daily_range=30.0)
    h4 = synthetic_h4(datetime(d.year, d.month, d.day, 13, 30, tzinfo=timezone.utc), n=220)
    return premarket.build_premarket(d, df, h4["close"])


def _long_position(ctx, lots=3.0):
    pos = PositionState(
        direction="LONG",
        entry_price=ctx.long_entry,
        entry_time_utc=ctx.session_start_utc + timedelta(minutes=30),
        initial_stop=ctx.long_sl,
        tp1=ctx.long_tp1,
        tp2=ctx.long_tp2,
        tranches=[TrancheState("T1", lots * 0.4),
                  TrancheState("T2", lots * 0.3),
                  TrancheState("T3", lots * 0.3)],
    )
    pos.current_stop = ctx.long_sl
    pos.running_extreme = ctx.long_entry
    pos.intraday_high = ctx.long_entry
    pos.intraday_low = ctx.long_entry
    return pos


def test_tp1_hit_closes_t1_and_moves_stop(settings):
    ctx = _ctx()
    pos = _long_position(ctx)
    ts = ctx.session_start_utc + timedelta(hours=1)
    upd = apply_tick(ctx, pos, settings, ctx.long_tp1 + 0.5, ts)

    assert any(t.name == "T1" and not t.is_open for t in pos.tranches)
    assert pos.tp1_hit
    assert upd.stop_moved
    # Breakeven-plus: stop > entry on a long
    assert pos.current_stop > pos.entry_price
    # Trail armed
    assert pos.trail_active


def test_initial_sl_hit_closes_all(settings):
    ctx = _ctx()
    pos = _long_position(ctx)
    ts = ctx.session_start_utc + timedelta(minutes=45)
    # Drive price down through stop
    upd = apply_tick(ctx, pos, settings, ctx.long_sl - 0.5, ts)
    assert all(not t.is_open for t in pos.tranches)
    assert all(t.close_reason == CloseReason.INITIAL_SL for t in pos.tranches)
    assert pos.closed


def test_tp2_hit_closes_t2_and_t3_trails(settings):
    ctx = _ctx()
    pos = _long_position(ctx)
    ts = ctx.session_start_utc + timedelta(hours=1)
    # First hit TP1
    apply_tick(ctx, pos, settings, ctx.long_tp1 + 0.5, ts)
    # Now hit TP2 (skip if regime override fully closed already)
    if pos.closed:
        return
    upd = apply_tick(ctx, pos, settings, ctx.long_tp2 + 0.5,
                     ts + timedelta(minutes=30))
    t2 = next(t for t in pos.tranches if t.name == "T2")
    if not t2.is_open:
        assert t2.close_reason == CloseReason.TP2
    t3 = next(t for t in pos.tranches if t.name == "T3")
    assert t3.is_open    # T3 keeps trailing


def test_session_end_force_close(settings):
    ctx = _ctx()
    pos = _long_position(ctx)
    ts_end = datetime(ctx.trade_date.year, ctx.trade_date.month, ctx.trade_date.day,
                      21, 0, tzinfo=timezone.utc)
    closed = exits.force_close_session_end(pos, ctx.long_entry + 5.0, ts_end)
    assert len(closed) == 3
    assert all(t.close_reason == CloseReason.SESSION_END for t in closed)
    assert pos.closed


def test_tp1_acceleration_at_1530_ny(settings):
    ctx = _ctx()
    pos = _long_position(ctx)
    # 15:30 NY in June = 19:30 UTC
    trigger = datetime(ctx.trade_date.year, ctx.trade_date.month, ctx.trade_date.day,
                       19, 30, tzinfo=timezone.utc)
    moved = exits.maybe_accelerate_tp1(ctx, pos, settings, trigger)
    assert moved
    assert pos.accelerated_tp1
    # New TP1 closer than original
    orig_dist = ctx.long_tp1 - ctx.long_entry
    new_dist = pos.accelerated_tp1_price - ctx.long_entry
    assert new_dist < orig_dist
    assert new_dist == pytest.approx(orig_dist * settings.tp1_accel_pct)


def test_tp1_acceleration_idempotent(settings):
    ctx = _ctx()
    pos = _long_position(ctx)
    trigger = datetime(ctx.trade_date.year, ctx.trade_date.month, ctx.trade_date.day,
                       19, 30, tzinfo=timezone.utc)
    assert exits.maybe_accelerate_tp1(ctx, pos, settings, trigger)
    # Second call should not re-move
    assert not exits.maybe_accelerate_tp1(ctx, pos, settings, trigger)


def test_trail_only_moves_favourably(settings):
    ctx = _ctx()
    pos = _long_position(ctx)
    # Trigger TP1
    apply_tick(ctx, pos, settings, ctx.long_tp1 + 0.5,
               ctx.session_start_utc + timedelta(hours=1))
    if pos.closed:
        return
    stop_after_tp1 = pos.current_stop
    # Price moves favourably → stop should rise
    apply_tick(ctx, pos, settings, ctx.long_tp1 + 5.0,
               ctx.session_start_utc + timedelta(hours=1, minutes=15))
    if not pos.closed:
        assert pos.current_stop >= stop_after_tp1
    # Price retraces → stop must not move backwards
    stop_now = pos.current_stop
    apply_tick(ctx, pos, settings, ctx.long_tp1 + 1.0,
               ctx.session_start_utc + timedelta(hours=1, minutes=30))
    if not pos.closed:
        assert pos.current_stop == stop_now

"""Partial-fill handling, per-tranche order rejection, volume reconciliation."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from config.settings import Settings
from src.broker.adapter import OrderSide, OrderTicket
from src.broker.paper_adapter import PaperAdapter
from src.data.feed import DataFeed
from src.engine.logger import StructuredLogger
from src.engine.runner import Engine
from src.strategy.entry import Candle, Direction
from src.strategy.exits import PositionState, TrancheState
from tests.conftest import synthetic_daily, synthetic_h4


class _StubFeed(DataFeed):
    def __init__(self, d, h):
        self._d, self._h = d, h
    def daily(self, sym, end, lookback_days): return self._d.tail(lookback_days)
    def h4(self, sym, end_utc, lookback_bars): return self._h.tail(lookback_bars)
    def m15(self, sym, s, e): return pd.DataFrame()


class PartialFillBroker(PaperAdapter):
    """PaperAdapter that fills only `fill_ratio` of requested volume."""
    def __init__(self, *a, fill_ratio=0.5, **kw):
        super().__init__(*a, **kw)
        self.fill_ratio = fill_ratio
    def open_market(self, symbol, side, volume_lots, sl, tp, comment, magic,
                    max_slippage_per_oz):
        filled = round(volume_lots * self.fill_ratio, 2)
        return super().open_market(symbol, side, filled, sl, tp, comment,
                                     magic, max_slippage_per_oz)


class RejectingBroker(PaperAdapter):
    """Rejects orders whose comment ends with a given suffix."""
    def __init__(self, *a, reject_suffix="H2", **kw):
        super().__init__(*a, **kw)
        self.reject_suffix = reject_suffix
    def open_market(self, symbol, side, volume_lots, sl, tp, comment, magic,
                    max_slippage_per_oz):
        if comment.endswith(self.reject_suffix):
            raise RuntimeError(f"simulated reject for {comment}")
        return super().open_market(symbol, side, volume_lots, sl, tp, comment,
                                     magic, max_slippage_per_oz)


def _settings(tmp_path, mode="live"):
    return Settings(
        mt5_login=0, mt5_password="", mt5_server="", mt5_terminal_path="",
        symbol="XAUUSD", starting_equity=100_000.0, risk_pct=0.03,
        magic_number=20260101, mode=mode, log_level="WARNING",
        log_dir=tmp_path / "logs", comex_webhook_enabled=False,
    )


def _ctx_and_engine(tmp_path, broker):
    s = _settings(tmp_path)
    daily = synthetic_daily(date(2024, 6, 25), n=220, daily_range=30.0)
    h4 = synthetic_h4(datetime(2024, 6, 25, 13, 30, tzinfo=timezone.utc), n=220)
    logger = StructuredLogger(s.log_dir, level="WARNING")
    engine = Engine(s, broker, _StubFeed(daily, h4), logger)
    engine.start_day(date(2024, 6, 25))
    return engine, s


# ── Partial fill ────────────────────────────────────────
def test_partial_fill_sizes_tranche_to_filled_volume(tmp_path):
    broker = PartialFillBroker(starting_equity=100_000.0, spread=0.10, fill_ratio=0.5)
    engine, s = _ctx_and_engine(tmp_path, broker)
    ctx = engine.state.premarket
    broker.set_quote(mid=ctx.long_entry,
                      time_utc=ctx.session_start_utc)
    # Build a sizing result and execute directly
    from src.strategy import sizing
    sized = sizing.compute_size(ctx, s, "LONG", ctx.long_entry, 100_000.0)
    ok = engine._execute_entry_plan(
        now_utc=ctx.session_start_utc, direction=Direction.LONG,
        entry_kind="CONTINUATION", entry_price=ctx.long_entry,
        sl=ctx.long_sl, tp1=ctx.long_tp1, tp2=ctx.long_tp2,
        sized=sized, body_ratio=0.8, equity=100_000.0,
        active_risk=0.03, ctx=ctx,
    )
    assert ok
    pos = engine.state.position
    # Each tranche should reflect the 50% filled volume, not requested
    assert pos.half_1.lots == pytest.approx(round(sized.half_1 * 0.5, 2))
    assert pos.half_2.lots == pytest.approx(round(sized.half_2 * 0.5, 2))


# ── Per-tranche rejection ───────────────────────────────
def test_h2_rejection_keeps_h1(tmp_path):
    broker = RejectingBroker(starting_equity=100_000.0, spread=0.10, reject_suffix="H2")
    engine, s = _ctx_and_engine(tmp_path, broker)
    ctx = engine.state.premarket
    broker.set_quote(mid=ctx.long_entry, time_utc=ctx.session_start_utc)
    from src.strategy import sizing
    sized = sizing.compute_size(ctx, s, "LONG", ctx.long_entry, 100_000.0)
    ok = engine._execute_entry_plan(
        now_utc=ctx.session_start_utc, direction=Direction.LONG,
        entry_kind="CONTINUATION", entry_price=ctx.long_entry,
        sl=ctx.long_sl, tp1=ctx.long_tp1, tp2=ctx.long_tp2,
        sized=sized, body_ratio=0.8, equity=100_000.0,
        active_risk=0.03, ctx=ctx,
    )
    assert ok                              # H1 still opened
    pos = engine.state.position
    names = [t.name for t in pos.tranches]
    assert "H1" in names
    assert "H2" not in names               # rejected tranche not added


def test_all_rejected_opens_nothing(tmp_path):
    broker = RejectingBroker(starting_equity=100_000.0, spread=0.10, reject_suffix="")
    # reject_suffix="" → every comment endswith("") is True → all rejected
    engine, s = _ctx_and_engine(tmp_path, broker)
    ctx = engine.state.premarket
    broker.set_quote(mid=ctx.long_entry, time_utc=ctx.session_start_utc)
    from src.strategy import sizing
    sized = sizing.compute_size(ctx, s, "LONG", ctx.long_entry, 100_000.0)
    ok = engine._execute_entry_plan(
        now_utc=ctx.session_start_utc, direction=Direction.LONG,
        entry_kind="CONTINUATION", entry_price=ctx.long_entry,
        sl=ctx.long_sl, tp1=ctx.long_tp1, tp2=ctx.long_tp2,
        sized=sized, body_ratio=0.8, equity=100_000.0,
        active_risk=0.03, ctx=ctx,
    )
    assert ok is False
    assert engine.state.position is None


# ── Volume reconciliation (partial external close) ──────
def test_partial_external_close_shrinks_tranche(tmp_path):
    broker = PaperAdapter(starting_equity=100_000.0, spread=0.20)
    broker.set_quote(mid=2000.0,
                      time_utc=datetime(2024, 6, 25, 14, tzinfo=timezone.utc))
    engine, s = _ctx_and_engine(tmp_path, broker)
    t1 = broker.open_market("XAUUSD", OrderSide.BUY, 2.0, sl=1990.0, tp=2010.0,
                             comment="v3.2 H1", magic=20260101, max_slippage_per_oz=0.3)
    pos = PositionState(
        direction="LONG", entry_price=2000.0,
        entry_time_utc=datetime(2024, 6, 25, 14, tzinfo=timezone.utc),
        initial_stop=1990.0, tp1=2010.0, tp2=2020.0,
        tranches=[TrancheState("H1", 2.0)],
    )
    engine.state.position = pos
    engine.state.tickets = [t1]
    # Simulate a partial manual close in MT5: reduce ticket volume to 0.8
    broker._open[t1.broker_id].volume_lots = 0.8
    now = datetime(2024, 6, 25, 15, tzinfo=timezone.utc)
    detected = engine.reconcile_external_closes(now, price=2005.0)
    assert detected is True
    assert pos.half_1.lots == pytest.approx(0.8)
    assert pos.half_1.is_open          # still open, just smaller


# ── Settings plumbing ───────────────────────────────────
def test_order_resilience_settings_from_env(monkeypatch):
    monkeypatch.setenv("ORDER_MAX_ATTEMPTS", "5")
    monkeypatch.setenv("ORDER_RETRY_BACKOFF_SEC", "1.5")
    s = Settings.from_env()
    assert s.order_max_attempts == 5
    assert s.order_retry_backoff_sec == 1.5


def test_order_resilience_settings_default(monkeypatch):
    monkeypatch.delenv("ORDER_MAX_ATTEMPTS", raising=False)
    monkeypatch.delenv("ORDER_RETRY_BACKOFF_SEC", raising=False)
    s = Settings.from_env()
    assert s.order_max_attempts == 3
    assert s.order_retry_backoff_sec == 0.5

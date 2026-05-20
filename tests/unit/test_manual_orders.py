"""Manual order controls + external-close detection."""
from __future__ import annotations

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from config.settings import Settings
from src.broker.adapter import OrderSide
from src.broker.paper_adapter import PaperAdapter
from src.data.feed import DataFeed
from src.engine.logger import StructuredLogger
from src.engine.runner import Engine
from src.strategy.exits import CloseReason, PositionState, TrancheState
from tests.conftest import synthetic_daily, synthetic_h4


class _StubFeed(DataFeed):
    def __init__(self, d, h):
        self._d, self._h = d, h
    def daily(self, sym, end, lookback_days): return self._d.tail(lookback_days)
    def h4(self, sym, end_utc, lookback_bars): return self._h.tail(lookback_bars)
    def m15(self, sym, s, e): return pd.DataFrame()


def _live_engine(tmp_path, mode="live"):
    s = Settings(
        mt5_login=0, mt5_password="", mt5_server="", mt5_terminal_path="",
        symbol="XAUUSD", starting_equity=100_000.0, risk_pct=0.03,
        magic_number=20260101, mode=mode, log_level="WARNING",
        log_dir=tmp_path / "logs", comex_webhook_enabled=False,
    )
    daily = synthetic_daily(date(2024, 6, 25), n=220, daily_range=30.0)
    h4 = synthetic_h4(datetime(2024, 6, 25, 13, 30, tzinfo=timezone.utc), n=220)
    broker = PaperAdapter(starting_equity=100_000.0, spread=0.20)
    broker.set_quote(mid=2000.0,
                      time_utc=datetime(2024, 6, 25, 14, 0, tzinfo=timezone.utc))
    logger = StructuredLogger(s.log_dir, level="WARNING")
    engine = Engine(s, broker, _StubFeed(daily, h4), logger)
    engine.start_day(date(2024, 6, 25))
    return engine, broker, s


def _attach_position(engine, broker):
    """Open two strategy tranches on the broker + mirror in engine state."""
    t1 = broker.open_market("XAUUSD", OrderSide.BUY, 1.0, sl=1990.0, tp=2010.0,
                             comment="v3.2 H1", magic=20260101, max_slippage_per_oz=0.3)
    t2 = broker.open_market("XAUUSD", OrderSide.BUY, 1.0, sl=1990.0, tp=2020.0,
                             comment="v3.2 H2", magic=20260101, max_slippage_per_oz=0.3)
    pos = PositionState(
        direction="LONG", entry_price=2000.0,
        entry_time_utc=datetime(2024, 6, 25, 14, tzinfo=timezone.utc),
        initial_stop=1990.0, tp1=2010.0, tp2=2020.0,
        tranches=[TrancheState("H1", 1.0), TrancheState("H2", 1.0)],
    )
    engine.state.position = pos
    engine.state.tickets = [t1, t2]
    return pos, t1, t2


# ── External-close detection ────────────────────────────
def test_external_close_detected_for_vanished_ticket(tmp_path):
    engine, broker, _ = _live_engine(tmp_path)
    pos, t1, t2 = _attach_position(engine, broker)
    # Simulate the user closing H1 in the MT5 terminal — ticket vanishes
    del broker._open[t1.broker_id]
    now = datetime(2024, 6, 25, 15, 0, tzinfo=timezone.utc)
    detected = engine.reconcile_external_closes(now, price=2005.0)
    assert detected is True
    h1 = next(t for t in pos.tranches if t.name == "H1")
    assert not h1.is_open
    assert h1.close_reason == CloseReason.EXTERNAL_CLOSE
    # H2 still open
    assert pos.half_2.is_open


def test_external_close_full_finalises_position(tmp_path):
    engine, broker, _ = _live_engine(tmp_path)
    pos, t1, t2 = _attach_position(engine, broker)
    # Both tickets vanish (whole position closed in terminal)
    del broker._open[t1.broker_id]
    del broker._open[t2.broker_id]
    now = datetime(2024, 6, 25, 15, 0, tzinfo=timezone.utc)
    detected = engine.reconcile_external_closes(now, price=2005.0)
    assert detected is True
    assert pos.closed
    assert engine.state.position is None   # cleared by bookkeeping


def test_external_close_noop_in_paper(tmp_path):
    engine, broker, _ = _live_engine(tmp_path, mode="paper")
    pos, t1, t2 = _attach_position(engine, broker)
    del broker._open[t1.broker_id]
    now = datetime(2024, 6, 25, 15, 0, tzinfo=timezone.utc)
    # Paper mode: external-close detection is a no-op
    assert engine.reconcile_external_closes(now, price=2005.0) is False
    assert pos.half_1.is_open   # untouched


def test_external_close_ignores_bot_own_closes(tmp_path):
    """A tranche the bot already marked closed isn't re-flagged."""
    engine, broker, _ = _live_engine(tmp_path)
    pos, t1, t2 = _attach_position(engine, broker)
    # Bot closes H1 itself (mark closed, ticket removed)
    pos.half_1.is_open = False
    pos.half_1.close_reason = CloseReason.TP1
    del broker._open[t1.broker_id]
    now = datetime(2024, 6, 25, 15, 0, tzinfo=timezone.utc)
    engine.reconcile_external_closes(now, price=2005.0)
    # H1 keeps its TP1 reason, not overwritten to EXTERNAL_CLOSE
    assert pos.half_1.close_reason == CloseReason.TP1


# ── Force close (manual "Close now") ────────────────────
def test_force_close_position(tmp_path):
    engine, broker, _ = _live_engine(tmp_path)
    pos, _, _ = _attach_position(engine, broker)
    now = datetime(2024, 6, 25, 15, 0, tzinfo=timezone.utc)
    ok = engine.force_close_position(now, price=2008.0)
    assert ok is True
    assert pos.closed
    assert all(t.close_reason == CloseReason.EXTERNAL_CLOSE for t in pos.tranches)


def test_force_close_noop_when_flat(tmp_path):
    engine, _, _ = _live_engine(tmp_path)
    now = datetime(2024, 6, 25, 15, 0, tzinfo=timezone.utc)
    assert engine.force_close_position(now, price=2000.0) is False

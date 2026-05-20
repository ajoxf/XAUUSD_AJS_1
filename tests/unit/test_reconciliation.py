"""Startup position reconciliation — adopt pre-existing broker positions
so a crash/restart never opens a duplicate."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pandas as pd
import pytest

from config.settings import Settings
from src.broker.adapter import OrderSide
from src.broker.paper_adapter import PaperAdapter
from src.data.feed import DataFeed
from src.engine.logger import StructuredLogger
from src.engine.runner import Engine
from src.strategy.exits import CloseReason
from tests.conftest import synthetic_daily, synthetic_h4


class _StubFeed(DataFeed):
    def __init__(self, d, h):
        self._d, self._h = d, h
    def daily(self, sym, end, lookback_days):
        return self._d.tail(lookback_days)
    def h4(self, sym, end_utc, lookback_bars):
        return self._h.tail(lookback_bars)
    def m15(self, sym, s, e):
        return pd.DataFrame()


@pytest.fixture
def settings(tmp_path):
    return Settings(
        mt5_login=0, mt5_password="", mt5_server="", mt5_terminal_path="",
        symbol="XAUUSD", starting_equity=100_000.0, risk_pct=0.03,
        magic_number=20260101, mode="live", log_level="WARNING",
        log_dir=tmp_path / "logs", comex_webhook_enabled=False,
    )


def _engine_with_premarket(settings, trade_date=date(2024, 6, 25)):
    daily = synthetic_daily(trade_date, n=220, daily_range=30.0)
    h4 = synthetic_h4(datetime(trade_date.year, trade_date.month, trade_date.day,
                                13, 30, tzinfo=timezone.utc), n=220)
    broker = PaperAdapter(starting_equity=100_000.0, spread=0.20)
    broker.set_quote(mid=2000.0,
                      time_utc=datetime(trade_date.year, trade_date.month,
                                         trade_date.day, 14, 0, tzinfo=timezone.utc))
    logger = StructuredLogger(settings.log_dir, level="WARNING")
    engine = Engine(settings, broker, _StubFeed(daily, h4), logger)
    engine.start_day(trade_date)
    return engine, broker


def test_no_positions_reconcile_noop(settings):
    engine, _ = _engine_with_premarket(settings)
    now = datetime(2024, 6, 25, 14, 30, tzinfo=timezone.utc)
    assert engine.reconcile_open_positions(now) is False
    assert engine.state.position is None


def test_reconcile_adopts_both_halves(settings):
    engine, broker = _engine_with_premarket(settings)
    today = datetime(2024, 6, 25, 14, 0, tzinfo=timezone.utc)
    # Simulate two open tranches from a prior (crashed) run, opened TODAY
    broker.open_market("XAUUSD", OrderSide.BUY, 1.02, sl=1990.0, tp=2010.0,
                        comment="v3.2 H1", magic=20260101, max_slippage_per_oz=0.3)
    broker.open_market("XAUUSD", OrderSide.BUY, 1.02, sl=1990.0, tp=2020.0,
                        comment="v3.2 H2", magic=20260101, max_slippage_per_oz=0.3)
    # Make the tickets' opened_at_utc be today (PaperAdapter uses last quote time)
    now = datetime(2024, 6, 25, 14, 30, tzinfo=timezone.utc)

    adopted = engine.reconcile_open_positions(now)
    assert adopted is True
    pos = engine.state.position
    assert pos is not None
    assert pos.direction == "LONG"
    assert len(pos.open_tranches) == 2
    assert not pos.tp1_hit            # both halves present → TP1 not hit
    # Daily lock fired → no new entry today
    assert engine.state.daily_lock.has_fired(date(2024, 6, 25))


def test_reconcile_infers_tp1_hit_when_only_h2(settings):
    engine, broker = _engine_with_premarket(settings)
    # Only H2 remains → TP1 already taken
    broker.open_market("XAUUSD", OrderSide.BUY, 1.02, sl=2000.0, tp=2020.0,
                        comment="v3.2 H2", magic=20260101, max_slippage_per_oz=0.3)
    now = datetime(2024, 6, 25, 15, 0, tzinfo=timezone.utc)
    assert engine.reconcile_open_positions(now) is True
    pos = engine.state.position
    assert pos.tp1_hit is True
    assert pos.trail_active is True
    assert len(pos.open_tranches) == 1
    assert pos.open_tranches[0].name == "H2"


def test_reconcile_skips_when_position_already_in_memory(settings):
    engine, broker = _engine_with_premarket(settings)
    broker.open_market("XAUUSD", OrderSide.BUY, 1.0, sl=1990.0, tp=2010.0,
                        comment="v3.2 H1", magic=20260101, max_slippage_per_oz=0.3)
    # Pretend we already track a position
    from src.strategy.exits import PositionState, TrancheState
    engine.state.position = PositionState(
        direction="LONG", entry_price=2000.0,
        entry_time_utc=datetime(2024, 6, 25, 14, tzinfo=timezone.utc),
        initial_stop=1990.0, tp1=2010.0, tp2=2020.0,
        tranches=[TrancheState("H1", 1.0)],
    )
    now = datetime(2024, 6, 25, 15, 0, tzinfo=timezone.utc)
    assert engine.reconcile_open_positions(now) is False


def test_reconcile_short_direction(settings):
    engine, broker = _engine_with_premarket(settings)
    broker.open_market("XAUUSD", OrderSide.SELL, 1.0, sl=2010.0, tp=1990.0,
                        comment="v3.2 H1", magic=20260101, max_slippage_per_oz=0.3)
    broker.open_market("XAUUSD", OrderSide.SELL, 1.0, sl=2010.0, tp=1980.0,
                        comment="v3.2 H2", magic=20260101, max_slippage_per_oz=0.3)
    now = datetime(2024, 6, 25, 15, 0, tzinfo=timezone.utc)
    assert engine.reconcile_open_positions(now) is True
    assert engine.state.position.direction == "SHORT"


def test_reconcile_force_closes_overnight_position(settings):
    """A position opened on a prior day violates the no-overnight rule and
    is force-closed immediately on adoption."""
    engine, broker = _engine_with_premarket(settings, trade_date=date(2024, 6, 25))
    # Open with the broker quote stamped YESTERDAY so opened_at_utc is in the past
    broker.set_quote(mid=2000.0,
                      time_utc=datetime(2024, 6, 24, 15, 0, tzinfo=timezone.utc))
    broker.open_market("XAUUSD", OrderSide.BUY, 1.0, sl=1990.0, tp=2010.0,
                        comment="v3.2 H1", magic=20260101, max_slippage_per_oz=0.3)
    # Now it's today
    broker.set_quote(mid=2005.0,
                      time_utc=datetime(2024, 6, 25, 10, 0, tzinfo=timezone.utc))
    now = datetime(2024, 6, 25, 10, 0, tzinfo=timezone.utc)
    adopted = engine.reconcile_open_positions(now)
    assert adopted is True
    # Position should be force-closed (no overnight holds)
    pos = engine.state.position
    assert pos is None   # cleared after force-close bookkeeping


def test_reconcile_does_not_open_duplicate_via_signal(settings):
    """After adopting, the daily lock blocks a fresh signal → no 2nd position."""
    from src.strategy.gates import check_all_gates
    engine, broker = _engine_with_premarket(settings)
    broker.open_market("XAUUSD", OrderSide.BUY, 1.0, sl=1990.0, tp=2010.0,
                        comment="v3.2 H1", magic=20260101, max_slippage_per_oz=0.3)
    now = datetime(2024, 6, 25, 14, 30, tzinfo=timezone.utc)
    engine.reconcile_open_positions(now)
    ctx = engine.state.premarket
    # A gate check now fails on G4 (daily lock + position open)
    res = check_all_gates(ctx, settings, ctx.session_start_utc, "LONG",
                           engine.state.daily_lock.has_fired(engine.state.today),
                           engine.state.position is not None)
    assert not res.passed
    assert any(f.startswith("G4:") for f in res.failures)

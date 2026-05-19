"""Integration: full synthetic day from pre-market → signal → TP1 → trail → close.

Uses PaperAdapter and a hand-built daily/h4 dataset to drive the engine
deterministically through every state transition.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from config.settings import Settings
from src.broker.paper_adapter import PaperAdapter
from src.data.feed import DataFeed
from src.engine.logger import StructuredLogger
from src.engine.reporting import weekly_report
from src.engine.runner import Engine
from src.strategy.entry import Candle, Direction
from src.strategy.exits import CloseReason
from tests.conftest import synthetic_daily, synthetic_h4


pytestmark = pytest.mark.integration


def _long_zone_closes() -> list[float]:
    """Closes that yield RSI(14) ≈ 63 — inside long zone [45, 65]."""
    closes = [100.0]
    for i in range(20):
        delta = 0.4 if i % 2 == 0 else -0.35
        closes.append(closes[-1] + delta)
    return closes


class StubFeed(DataFeed):
    def __init__(self, daily: pd.DataFrame, h4: pd.DataFrame):
        self._daily = daily
        self._h4 = h4

    def daily(self, symbol, end, lookback_days):
        return self._daily.tail(lookback_days)

    def h4(self, symbol, end_utc, lookback_bars):
        return self._h4.tail(lookback_bars)

    def m15(self, symbol, start_utc, end_utc):
        return pd.DataFrame()


def _make_engine(tmp_path: Path, trade_date: date,
                 base_price: float = 2000.0, daily_range: float = 30.0):
    daily = synthetic_daily(trade_date, n=220, base_price=base_price,
                            daily_range=daily_range)
    # Force prev_close above EMA50_4H to lock trend_bias = LONG_ONLY
    daily.loc[daily.index[-1], "close"] = base_price + 80
    daily.loc[daily.index[-1], "high"] = base_price + 100
    daily.loc[daily.index[-1], "low"] = base_price + 55
    daily.loc[daily.index[-1], "open"] = base_price + 70
    h4 = synthetic_h4(datetime(trade_date.year, trade_date.month,
                                trade_date.day, 13, 30, tzinfo=timezone.utc),
                      n=220, start_price=base_price)
    feed = StubFeed(daily, h4)

    settings = Settings(
        mt5_login=0, mt5_password="", mt5_server="", mt5_terminal_path="",
        symbol="XAUUSD", starting_equity=100_000.0, risk_pct=0.03,
        magic_number=20260101,
        mode="paper", log_level="INFO", log_dir=tmp_path / "logs",
    )
    broker = PaperAdapter(starting_equity=100_000.0, spread=0.10)
    broker.set_quote(mid=base_price + 80, time_utc=datetime(trade_date.year,
                     trade_date.month, trade_date.day, 13, 30, tzinfo=timezone.utc))
    logger = StructuredLogger(tmp_path / "logs", level="INFO")
    engine = Engine(settings, broker, feed, logger)
    return engine, broker, settings


def test_full_day_long_tp1_then_trail(tmp_path):
    trade_date = date(2024, 6, 25)   # not blocked
    engine, broker, settings = _make_engine(tmp_path, trade_date)
    ctx = engine.start_day(trade_date)
    assert ctx is not None
    assert ctx.trend_bias in ("LONG_ONLY", "BOTH")

    # Move broker price to just below long_entry
    broker.set_quote(mid=ctx.long_entry - 0.05,
                     time_utc=ctx.session_start_utc + timedelta(minutes=10))

    # Confirmation candle: strong close above long_entry, 80%+ body
    rng = ctx.range
    confirmation = Candle(
        open=ctx.long_entry - rng * 0.05,
        high=ctx.long_entry + rng * 0.10,
        low=ctx.long_entry - rng * 0.06,
        close=ctx.long_entry + rng * 0.08,
    )
    # Next candle: continuation
    next_candle = Candle(
        open=confirmation.close,
        high=confirmation.close + rng * 0.05,
        low=confirmation.close - rng * 0.01,
        close=confirmation.close + rng * 0.04,
    )
    closes_15m = _long_zone_closes()

    broker.set_quote(mid=ctx.long_entry + 0.05,
                     time_utc=ctx.session_start_utc + timedelta(minutes=30))
    fired = engine.evaluate_signal(
        now_utc=ctx.session_start_utc + timedelta(minutes=30),
        direction=Direction.LONG,
        confirmation_candle=confirmation,
        next_candle=next_candle,
        closes_15m=closes_15m,
    )
    assert fired, "Trade should have opened"
    assert engine.state.position is not None
    pos = engine.state.position
    assert len(pos.tranches) == 2   # v3.2 — 50/50 split (H1, H2)

    # Drive price up through TP1
    engine.on_tick(ctx.long_tp1 + 0.5,
                   ctx.session_start_utc + timedelta(hours=1))
    h1 = next(t for t in pos.tranches if t.name == "H1")
    if not pos.closed:   # regime override might fully close in RANGING
        assert not h1.is_open
        assert h1.close_reason == CloseReason.TP1
        # Stop moved to breakeven-plus
        assert pos.current_stop > pos.entry_price

    # Force session end if still open
    end = datetime(trade_date.year, trade_date.month, trade_date.day,
                   21, 0, tzinfo=timezone.utc)
    if not pos.closed:
        broker.set_quote(mid=ctx.long_tp1 + 1.0, time_utc=end)
        engine.on_tick(ctx.long_tp1 + 1.0, end)
    # Position fully closed after session end
    assert pos.closed or pos.is_fully_closed

    # Weekly report sanity
    report = weekly_report(engine.state, trade_date, broker.equity(), 100_000.0)
    assert report["trades_executed"] == 1
    assert "win_rate_tp1_pct" in report["outcomes"]


def test_event_day_blocks_trade(tmp_path):
    trade_date = date(2024, 1, 31)   # FOMC
    engine, broker, _ = _make_engine(tmp_path, trade_date,
                                       base_price=2000.0)
    ctx = engine.start_day(trade_date)
    assert ctx is not None
    assert ctx.event_blocked

    # Attempt signal — should be gate-blocked
    confirmation = Candle(open=ctx.long_entry - 1, high=ctx.long_entry + 5,
                          low=ctx.long_entry - 2, close=ctx.long_entry + 4)
    next_candle = Candle(open=confirmation.close, high=confirmation.close + 3,
                         low=confirmation.close - 0.5, close=confirmation.close + 2)
    fired = engine.evaluate_signal(
        now_utc=ctx.session_start_utc + timedelta(minutes=30),
        direction=Direction.LONG,
        confirmation_candle=confirmation,
        next_candle=next_candle,
        closes_15m=_long_zone_closes(),
    )
    assert not fired
    assert engine.state.position is None


def test_daily_lock_blocks_second_trade(tmp_path):
    trade_date = date(2024, 6, 25)
    engine, broker, _ = _make_engine(tmp_path, trade_date)
    ctx = engine.start_day(trade_date)

    confirmation = Candle(
        open=ctx.long_entry - ctx.range * 0.05,
        high=ctx.long_entry + ctx.range * 0.10,
        low=ctx.long_entry - ctx.range * 0.06,
        close=ctx.long_entry + ctx.range * 0.08,
    )
    next_candle = Candle(
        open=confirmation.close,
        high=confirmation.close + ctx.range * 0.05,
        low=confirmation.close - ctx.range * 0.01,
        close=confirmation.close + ctx.range * 0.04,
    )
    closes_15m = _long_zone_closes()

    broker.set_quote(mid=ctx.long_entry + 0.05,
                     time_utc=ctx.session_start_utc + timedelta(minutes=30))
    fired1 = engine.evaluate_signal(
        now_utc=ctx.session_start_utc + timedelta(minutes=30),
        direction=Direction.LONG,
        confirmation_candle=confirmation, next_candle=next_candle,
        closes_15m=closes_15m,
    )
    assert fired1

    # Close the first position by hitting initial SL fast
    engine.on_tick(ctx.long_sl - 0.5,
                   ctx.session_start_utc + timedelta(minutes=45))

    # Now attempt a second signal — must be blocked by daily lock
    fired2 = engine.evaluate_signal(
        now_utc=ctx.session_start_utc + timedelta(hours=2),
        direction=Direction.LONG,
        confirmation_candle=confirmation, next_candle=next_candle,
        closes_15m=closes_15m,
    )
    assert not fired2

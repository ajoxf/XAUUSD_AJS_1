"""Daily-resolution backtest. Approximate — uses daily OHLC heuristic to
determine which level (TP1 / TP2 / SL) was hit first.

Limitations (surfaced in the UI):
- No intraday RSI / re-test confirmation: those gates are treated as PASS.
- Outcome heuristic: if close > open we assume O→H→L→C path (TP1 before SL);
  if close < open we assume O→L→H→C path (SL before TP1 if SL is below).
- 3% sizing applied per trade; equity compounds.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Dict, List, Optional, Tuple

import pandas as pd

from config.settings import Settings
from src.data.yfinance_feed import YFinanceFeed
from src.strategy import gates, premarket, sizing
from src.strategy.premarket import PremarketContext
from src.strategy.session import session_window_utc


@dataclass
class BacktestTrade:
    trade_date: date
    direction: str
    regime: str
    entry_price: float
    sl: float
    tp1: float
    tp2: float
    lots: float
    outcome: str          # 'TP1' | 'TP2' | 'SL' | 'SESSION_END' | 'NO_SIGNAL'
    exit_price: float
    pnl: float
    equity_after: float


@dataclass
class BacktestResult:
    starting_equity: float
    final_equity: float
    trades: List[BacktestTrade] = field(default_factory=list)
    equity_curve: List[Tuple[date, float]] = field(default_factory=list)
    blocked_days: int = 0
    no_signal_days: int = 0
    skipped_days: int = 0   # days with insufficient history

    @property
    def n_trades(self) -> int:
        return sum(1 for t in self.trades if t.outcome != "NO_SIGNAL")

    @property
    def tp1_win_rate(self) -> float:
        actionable = [t for t in self.trades if t.outcome != "NO_SIGNAL"]
        if not actionable:
            return 0.0
        wins = sum(1 for t in actionable if t.outcome in ("TP1", "TP2"))
        return wins / len(actionable) * 100.0

    @property
    def total_return_pct(self) -> float:
        if self.starting_equity <= 0:
            return 0.0
        return (self.final_equity - self.starting_equity) / self.starting_equity * 100.0

    @property
    def max_drawdown_pct(self) -> float:
        if not self.equity_curve:
            return 0.0
        peak = self.equity_curve[0][1]
        max_dd = 0.0
        for _, eq in self.equity_curve:
            if eq > peak:
                peak = eq
            dd = (peak - eq) / peak * 100.0 if peak > 0 else 0.0
            if dd > max_dd:
                max_dd = dd
        return max_dd

    @property
    def total_pnl(self) -> float:
        return self.final_equity - self.starting_equity


def _resolve_outcome(daily_row: pd.Series, ctx: PremarketContext,
                     direction: str) -> Tuple[str, float]:
    """Returns (outcome, exit_price). Outcome ∈ TP1/TP2/SL/SESSION_END."""
    h = float(daily_row["high"])
    l = float(daily_row["low"])
    o = float(daily_row["open"])
    c = float(daily_row["close"])
    bullish = c >= o

    if direction == "LONG":
        entry = ctx.long_entry
        sl, tp1, tp2 = ctx.long_sl, ctx.long_tp1, ctx.long_tp2
        # Did intraday high actually reach entry?
        if h < entry:
            return "NO_SIGNAL", 0.0
        # Heuristic: bullish day → up-first; bearish → down-first
        if bullish:
            # O→H→L→C : TP1 likely hit before SL
            if h >= tp2:
                return "TP2", tp2
            if h >= tp1:
                return "TP1", tp1
            return "SESSION_END", c
        else:
            # O→L→H→C : SL more likely first if low reaches it
            # but only count SL if low went below sl AFTER entering (entry < h)
            if l <= sl:
                return "SL", sl
            if h >= tp1:
                return "TP1", tp1
            return "SESSION_END", c

    # SHORT
    entry = ctx.short_entry
    sl, tp1, tp2 = ctx.short_sl, ctx.short_tp1, ctx.short_tp2
    if l > entry:
        return "NO_SIGNAL", 0.0
    if not bullish:
        # O→L→H→C : TP1 likely hit before SL
        if l <= tp2:
            return "TP2", tp2
        if l <= tp1:
            return "TP1", tp1
        return "SESSION_END", c
    else:
        # O→H→L→C : SL more likely first
        if h >= sl:
            return "SL", sl
        if l <= tp1:
            return "TP1", tp1
        return "SESSION_END", c


def run_backtest(start: date, end: date, settings: Settings,
                 progress_cb=None) -> BacktestResult:
    feed = YFinanceFeed()
    # Need at least max(SMA200, atr_long_period) bars before `start`,
    # plus headroom for weekends/holidays
    lookback_buffer = max(220, settings.atr_long_period + 20)
    df = feed.daily(settings.symbol, end + timedelta(days=1),
                    lookback_days=(end - start).days + lookback_buffer)
    if isinstance(df.index, pd.DatetimeIndex):
        df.index = df.index.date

    # 4H series — fetch once over the full window
    h4 = feed.h4(settings.symbol,
                 pd.Timestamp(end) + pd.Timedelta(days=1),
                 lookback_bars=(end - start).days * 6 + 60 * 6)

    result = BacktestResult(starting_equity=settings.starting_equity,
                              final_equity=settings.starting_equity)
    equity = settings.starting_equity
    result.equity_curve.append((start, equity))

    trade_dates = [d for d in df.index if start <= d <= end]
    total = len(trade_dates)
    for idx, trade_date in enumerate(trade_dates):
        if progress_cb is not None:
            progress_cb(idx / max(total, 1), trade_date)

        # Build pre-market context using only data through previous day
        prior = df[df.index < trade_date].tail(60)
        if len(prior) < 51:
            result.skipped_days += 1
            continue
        # 4H closes up to session start of trade_date
        session_start, _ = session_window_utc(trade_date)
        h4_window = h4[h4.index < pd.Timestamp(session_start, tz="UTC")].tail(60)
        if len(h4_window) < 50:
            result.skipped_days += 1
            continue
        try:
            ctx = premarket.build_premarket(
                trade_date, prior, h4_window["close"],
                atr_short_period=settings.atr_short_period,
                atr_long_period=settings.atr_long_period,
            )
        except ValueError:
            result.skipped_days += 1
            continue

        # Daily-resolvable gates: event + ATR + trend_bias + range floor
        if ctx.event_blocked:
            result.blocked_days += 1
            continue
        if ctx.range < ctx.atr_20:
            result.blocked_days += 1
            continue
        if ctx.range > settings.atr_cap_multiplier * ctx.atr_20:
            result.blocked_days += 1
            continue
        if ctx.range < settings.min_range_floor:
            result.blocked_days += 1
            continue
        if ctx.trend_bias == "LONG_ONLY":
            direction = "LONG"
        elif ctx.trend_bias == "SHORT_ONLY":
            direction = "SHORT"
        else:
            # Use prior day's drift to pick a direction (mimics 'BOTH' ambiguity)
            direction = "LONG" if ctx.prev_close > ctx.prev_open else "SHORT"

        # Need the day's OHLC row to resolve outcome
        if trade_date not in df.index:
            continue
        day_row = df.loc[trade_date]
        outcome, exit_price = _resolve_outcome(day_row, ctx, direction)
        if outcome == "NO_SIGNAL":
            result.no_signal_days += 1
            continue

        # Size the trade
        entry = ctx.long_entry if direction == "LONG" else ctx.short_entry
        sized = sizing.compute_size(ctx, settings, direction, entry, equity)

        # P&L computation: tranches share entry, exit at outcome's level
        # Approximation: tranche_1 closes at tp1 (if reached), tranche_2 at tp2
        # (if reached), tranche_3 at outcome level. Tranches stopped out share
        # the SL/SESSION_END price.
        pnl = _resolve_pnl(direction, outcome, exit_price, sized.tranche_1,
                            sized.tranche_2, sized.tranche_3, entry, ctx, settings)
        equity += pnl

        trade = BacktestTrade(
            trade_date=trade_date,
            direction=direction,
            regime=ctx.regime,
            entry_price=entry,
            sl=ctx.long_sl if direction == "LONG" else ctx.short_sl,
            tp1=ctx.long_tp1 if direction == "LONG" else ctx.short_tp1,
            tp2=ctx.long_tp2 if direction == "LONG" else ctx.short_tp2,
            lots=sized.lots_final,
            outcome=outcome,
            exit_price=exit_price,
            pnl=round(pnl, 2),
            equity_after=round(equity, 2),
        )
        result.trades.append(trade)
        result.equity_curve.append((trade_date, equity))

    result.final_equity = equity
    if progress_cb is not None:
        progress_cb(1.0, trade_dates[-1] if trade_dates else end)
    return result


def _resolve_pnl(direction: str, outcome: str, exit_price: float,
                 t1: float, t2: float, t3: float, entry: float,
                 ctx: PremarketContext, settings: Settings) -> float:
    """Compute total P&L for the trade given outcome.

    SL: all tranches close at SL.
    TP1: T1 at TP1, T2+T3 close at SESSION_END proxy (use exit_price=close).
        For backtest we conservatively close them at TP1 as well (no trail
        modelled here — that's a TODO surfaced in the UI).
    TP2: T1 at TP1, T2 at TP2, T3 at TP2 (conservative — no trail extension).
    SESSION_END: all tranches at exit_price.
    """
    sign = 1 if direction == "LONG" else -1
    tp1_price = ctx.long_tp1 if direction == "LONG" else ctx.short_tp1
    tp2_price = ctx.long_tp2 if direction == "LONG" else ctx.short_tp2
    sl_price = ctx.long_sl if direction == "LONG" else ctx.short_sl
    contract = settings.contract_size

    if outcome == "SL":
        per_oz = sign * (sl_price - entry)
        return per_oz * contract * (t1 + t2 + t3)

    if outcome == "TP1":
        # T1 wins at TP1; remaining tranches close at TP1 as well (worst case)
        per_oz_t1 = sign * (tp1_price - entry)
        return per_oz_t1 * contract * (t1 + t2 + t3)

    if outcome == "TP2":
        per_oz_t1 = sign * (tp1_price - entry)
        per_oz_t2 = sign * (tp2_price - entry)
        return (per_oz_t1 * contract * t1
                + per_oz_t2 * contract * (t2 + t3))

    # SESSION_END
    per_oz = sign * (exit_price - entry)
    return per_oz * contract * (t1 + t2 + t3)

"""Aggregate runtime state for the engine — v3.2."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import List, Optional

from src.broker.adapter import OrderTicket
from src.strategy.comex_volume import ComexVolumeTracker
from src.strategy.exits import PositionState
from src.strategy.premarket import PremarketContext
from src.strategy.safety import (CircuitBreaker, ConsecutiveLossCounter,
                                  DailyTradeLock, WinRateMonitor)
from src.strategy.sizing import SizingResult


@dataclass
class PendingTrade:
    """A trade plan computed by the engine, waiting for operator approval.

    All sizing and entry-price decisions were made at `created_at_utc`;
    the orders won't reach the broker until `confirm_pending_trade()` is
    called (or the plan expires)."""
    direction: str                       # 'LONG' | 'SHORT'
    entry_kind: str                      # 'CONTINUATION' | 'RETEST'
    entry_price: float                   # ask (long) / bid (short) at decision time
    sl: float
    tp1: float
    tp2: float
    sizing: SizingResult
    equity_at_decision: float
    active_risk_pct: float
    created_at_utc: datetime
    expires_at_utc: datetime


@dataclass
class EngineState:
    starting_equity: float
    peak_equity: float
    daily_lock: DailyTradeLock = field(default_factory=DailyTradeLock)
    loss_counter: ConsecutiveLossCounter = field(default_factory=ConsecutiveLossCounter)
    win_rate_monitor: WinRateMonitor = None  # type: ignore[assignment]
    circuit_breaker: CircuitBreaker = None    # type: ignore[assignment]
    comex_tracker: Optional[ComexVolumeTracker] = None   # v3.2 Opt 1

    today: Optional[date] = None
    premarket: Optional[PremarketContext] = None

    position: Optional[PositionState] = None
    tickets: List[OrderTicket] = field(default_factory=list)

    # Set when require_trade_confirmation is on. While non-None, the
    # engine refuses to evaluate new signals until the operator confirms
    # or cancels — or the plan auto-expires.
    pending_trade: Optional[PendingTrade] = None

    # v3.2 — carried context for Opt 2 (back-to-back TP2 extension)
    prev_session_close_type: Optional[str] = None

    # v3.2 — track 15-min closes since TP1 hit for Opt 6 RSI trim
    closes_15m_post_tp1: List[float] = field(default_factory=list)

    # Per-week aggregates
    week_trades: int = 0
    week_wins_tp1: int = 0
    week_wins_tp2: int = 0
    week_sls: int = 0
    week_regime_exits: int = 0
    week_tp1_accels: int = 0
    week_wednesday_accels: int = 0          # v3.2 Opt 5
    week_comex_vol_exits: int = 0           # v3.2 Opt 1
    week_rsi_trims: int = 0                 # v3.2 Opt 6
    week_back_to_back_tp2: int = 0          # v3.2 Opt 2
    week_high_atr_tp2: int = 0              # v3.2 Opt 3
    week_session_close_half2: int = 0
    week_session_close_full: int = 0

    week_b_fails: int = 0
    week_c_fails: int = 0
    week_d_fails: int = 0

    gate_block_event: int = 0
    gate_block_atr_floor: int = 0
    gate_block_atr_cap: int = 0
    gate_block_trend: int = 0
    gate_block_sma200: int = 0              # v3.2 Opt 4
    gate_block_daily_lock: int = 0
    gate_block_no_signal: int = 0

    def update_peak(self, equity: float) -> None:
        if equity > self.peak_equity:
            self.peak_equity = equity

    def current_drawdown_pct(self, equity: float) -> float:
        if self.peak_equity <= 0:
            return 0.0
        return max(0.0, (self.peak_equity - equity) / self.peak_equity * 100.0)

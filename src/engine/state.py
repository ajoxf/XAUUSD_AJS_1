"""Aggregate runtime state for the engine - v3.2."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from src.broker.adapter import OrderTicket
from src.strategy.comex_volume import ComexVolumeTracker
from src.strategy.exits import PositionState
from src.strategy.premarket import PremarketContext
from src.strategy.safety import (CircuitBreaker, ConsecutiveLossCounter,
                                  DailyTradeLock, WinRateMonitor)


@dataclass
class PendingEntry:
    """A signal that has cleared every filter but is awaiting human approval
    before the engine sends orders to the broker."""
    direction: str
    entry_price: float
    entry_kind: str
    sl: float
    tp1: float
    tp2: float
    half_1_lots: float
    half_2_lots: float
    risk_amount: float
    actual_risk: float
    deviation_pct: float
    sizing_audit: Dict[str, Any]
    created_at_utc: datetime
    timeout_at_utc: datetime
    confirmed: bool = False
    cancelled: bool = False
    expired: bool = False

    def is_terminal(self) -> bool:
        return self.confirmed or self.cancelled or self.expired

    def remaining_seconds(self, now_utc: datetime) -> float:
        return max(0.0, (self.timeout_at_utc - now_utc).total_seconds())


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

    # v3.2 - confirm-trade workflow (manual approval before order send)
    pending_entry: Optional[PendingEntry] = None

    # v3.2 - carried context for Opt 2 (back-to-back TP2 extension)
    prev_session_close_type: Optional[str] = None

    # v3.2 - track 15-min closes since TP1 hit for Opt 6 RSI trim
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

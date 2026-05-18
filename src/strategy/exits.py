"""Three-tranche exit management — spec §6 + §7."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional

from config.flags import FLAGS
from config.settings import Settings
from src.strategy import session
from src.strategy.premarket import PremarketContext


class CloseReason(str, Enum):
    TP1 = "TP1"
    TP2 = "TP2"
    TRAIL_STOP = "TRAIL_STOP"
    INITIAL_SL = "INITIAL_SL"
    SESSION_END = "SESSION_END"
    REGIME_OVERRIDE = "REGIME_OVERRIDE"


@dataclass
class TrancheState:
    name: str             # 'T1' | 'T2' | 'T3'
    lots: float
    is_open: bool = True
    close_price: Optional[float] = None
    close_reason: Optional[CloseReason] = None
    close_time_utc: Optional[datetime] = None


@dataclass
class PositionState:
    direction: str
    entry_price: float
    entry_time_utc: datetime
    initial_stop: float
    tp1: float
    tp2: float
    tranches: List[TrancheState]

    tp1_hit: bool = False
    tp2_hit: bool = False
    tp1_hit_price: Optional[float] = None
    tp1_hit_time_utc: Optional[datetime] = None
    accelerated_tp1: bool = False
    accelerated_tp1_price: Optional[float] = None

    current_stop: float = 0.0
    trail_active: bool = False
    trail_distance: float = 0.0
    running_extreme: float = 0.0   # high for longs, low for shorts
    intraday_high: float = 0.0
    intraday_low: float = 0.0

    closed: bool = False

    def __post_init__(self):
        if self.current_stop == 0.0:
            self.current_stop = self.initial_stop
        if self.running_extreme == 0.0:
            self.running_extreme = self.entry_price
        if self.intraday_high == 0.0:
            self.intraday_high = self.entry_price
        if self.intraday_low == 0.0:
            self.intraday_low = self.entry_price

    @property
    def open_tranches(self) -> List[TrancheState]:
        return [t for t in self.tranches if t.is_open]

    @property
    def is_fully_closed(self) -> bool:
        return all(not t.is_open for t in self.tranches)


@dataclass
class TickUpdate:
    """Output of `apply_tick`: tranches closed during this tick + new stop."""
    closed: List[TrancheState] = field(default_factory=list)
    stop_moved: bool = False
    new_stop: Optional[float] = None
    accelerated: bool = False


def _close_tranche(t: TrancheState, price: float, reason: CloseReason,
                   ts: datetime) -> TrancheState:
    t.is_open = False
    t.close_price = price
    t.close_reason = reason
    t.close_time_utc = ts
    return t


def _compute_trail_distance(ctx: PremarketContext, pos: PositionState) -> float:
    if not FLAGS.FILTER_D_TRAILING_STOP:
        return 0.0
    intraday_range_at_tp1 = pos.intraday_high - pos.intraday_low
    base = ctx.range
    if FLAGS.OPT_REGIME_DETECTOR and ctx.regime == "RANGING":
        fib = 0.236
    else:
        fib = 0.382
    return fib * max(base, intraday_range_at_tp1)


def apply_tick(
    ctx: PremarketContext,
    pos: PositionState,
    settings: Settings,
    tick_price: float,
    tick_time_utc: datetime,
) -> TickUpdate:
    """Idempotent application of one tick (or 1m bar) to the position."""
    update = TickUpdate()
    if pos.closed or pos.is_fully_closed:
        return update

    # Track intraday extremes
    if tick_price > pos.intraday_high:
        pos.intraday_high = tick_price
    if tick_price < pos.intraday_low:
        pos.intraday_low = tick_price

    direction = pos.direction
    is_long = direction == "LONG"

    # ── Step A: Stop-loss check (applies before TP) ─────────────
    sl_hit = (tick_price <= pos.current_stop) if is_long else (tick_price >= pos.current_stop)
    if sl_hit:
        # Determine reason based on stage
        if not pos.tp1_hit:
            reason = CloseReason.INITIAL_SL
        else:
            reason = CloseReason.TRAIL_STOP if pos.trail_active else CloseReason.INITIAL_SL
        for t in pos.open_tranches:
            update.closed.append(_close_tranche(t, pos.current_stop, reason, tick_time_utc))
        pos.closed = True
        return update

    # ── Step B: TP1 hit (closes T1 + arms breakeven-plus + trail) ─
    if not pos.tp1_hit:
        target_tp1 = pos.accelerated_tp1_price if pos.accelerated_tp1 else pos.tp1
        tp1_hit = (tick_price >= target_tp1) if is_long else (tick_price <= target_tp1)
        if tp1_hit:
            pos.tp1_hit = True
            pos.tp1_hit_price = target_tp1
            pos.tp1_hit_time_utc = tick_time_utc
            # Close T1
            t1 = next((t for t in pos.tranches if t.name == "T1" and t.is_open), None)
            if t1 is not None:
                update.closed.append(_close_tranche(t1, target_tp1, CloseReason.TP1, tick_time_utc))
            # Update running extreme to be at TP1 hit
            pos.running_extreme = target_tp1 if is_long else target_tp1
            # Breakeven-plus stop on remaining tranches
            if FLAGS.OPT_BREAKEVEN_PLUS:
                tp1_gain = abs(target_tp1 - pos.entry_price)
                if is_long:
                    new_stop = pos.entry_price + settings.breakeven_plus_pct * tp1_gain
                else:
                    new_stop = pos.entry_price - settings.breakeven_plus_pct * tp1_gain
            else:
                new_stop = pos.entry_price
            pos.current_stop = new_stop
            update.stop_moved = True
            update.new_stop = new_stop
            # Arm trail
            pos.trail_distance = _compute_trail_distance(ctx, pos)
            pos.trail_active = pos.trail_distance > 0

            # Regime override: RANGING → close all tranches at TP1
            if FLAGS.OPT_REGIME_DETECTOR and ctx.regime == "RANGING":
                for t in pos.open_tranches:
                    update.closed.append(_close_tranche(
                        t, target_tp1, CloseReason.REGIME_OVERRIDE, tick_time_utc))
                pos.closed = True
            return update

    # ── Step C: TP2 hit (closes T2; T3 trails on) ───────────────
    if pos.tp1_hit and not pos.tp2_hit:
        tp2_hit = (tick_price >= pos.tp2) if is_long else (tick_price <= pos.tp2)
        if tp2_hit:
            pos.tp2_hit = True
            t2 = next((t for t in pos.tranches if t.name == "T2" and t.is_open), None)
            if t2 is not None:
                update.closed.append(_close_tranche(t2, pos.tp2, CloseReason.TP2, tick_time_utc))
            # Update extreme to TP2 to anchor trail going forward
            pos.running_extreme = pos.tp2

    # ── Step D: Trail update for any open tranches after TP1 ────
    if pos.tp1_hit and pos.trail_active:
        if is_long:
            if tick_price > pos.running_extreme:
                pos.running_extreme = tick_price
            new_trail = pos.running_extreme - pos.trail_distance
            if new_trail > pos.current_stop:
                pos.current_stop = new_trail
                update.stop_moved = True
                update.new_stop = new_trail
        else:
            if tick_price < pos.running_extreme:
                pos.running_extreme = tick_price
            new_trail = pos.running_extreme + pos.trail_distance
            if new_trail < pos.current_stop:
                pos.current_stop = new_trail
                update.stop_moved = True
                update.new_stop = new_trail

    return update


def maybe_accelerate_tp1(
    ctx: PremarketContext,
    pos: PositionState,
    settings: Settings,
    now_utc: datetime,
) -> bool:
    """At 15:30 NY local: if position open and TP1 not hit, move TP1 to 80%
    of original distance. Idempotent."""
    if not FLAGS.OPT_TP1_TIME_ACCEL:
        return False
    if pos.closed or pos.tp1_hit or pos.accelerated_tp1:
        return False
    trigger_utc = session.ny_time_to_utc(ctx.trade_date, 15, 30)
    if now_utc < trigger_utc:
        return False
    original_distance = abs(pos.tp1 - pos.entry_price)
    new_dist = original_distance * settings.tp1_accel_pct
    if pos.direction == "LONG":
        pos.accelerated_tp1_price = pos.entry_price + new_dist
    else:
        pos.accelerated_tp1_price = pos.entry_price - new_dist
    pos.accelerated_tp1 = True
    return True


def force_close_session_end(
    pos: PositionState,
    last_price: float,
    now_utc: datetime,
) -> List[TrancheState]:
    """Spec §7 — 21:00 UTC hard close. Caller passes any open price."""
    closed: List[TrancheState] = []
    for t in pos.open_tranches:
        closed.append(_close_tranche(t, last_price, CloseReason.SESSION_END, now_utc))
    pos.closed = True
    return closed

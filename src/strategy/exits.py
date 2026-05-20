"""Trade management - spec §6 + §7 v3.2.

50/50 exit structure:
- Half 1 closes at TP1 → breakeven-plus stop on Half 2 → trail activates
- Half 2 targets TP2, with: COMEX volume fade exit, RSI post-TP1 trim,
  Wednesday early TP1 acceleration
- 20:55 UTC partial-close decision tree, 21:00 UTC absolute safety net
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional, Sequence

from config.flags import FLAGS
from config.settings import Settings
from src.strategy import indicators, session
from src.strategy.comex_volume import ComexVolumeTracker
from src.strategy.premarket import PremarketContext


class CloseReason(str, Enum):
    TP1 = "TP1"
    TP2 = "TP2"
    TRAIL_STOP = "TRAIL_STOP"
    INITIAL_SL = "INITIAL_SL"
    SESSION_CLOSE_FULL_NO_TP1 = "SESSION_CLOSE_FULL_NO_TP1"
    SESSION_CLOSE_HALF2 = "SESSION_CLOSE_HALF2"
    SESSION_CLOSE_RESIDUAL = "SESSION_CLOSE_RESIDUAL"
    SESSION_END = "SESSION_END"
    REGIME_OVERRIDE = "REGIME_OVERRIDE"
    COMEX_VOL_FADE = "COMEX_VOL_FADE"
    RSI_TRIM = "RSI_TRIM"
    EXTERNAL_CLOSE = "EXTERNAL_CLOSE"   # closed in MT5 terminal / by broker


@dataclass
class TrancheState:
    """Half of the position in v3.2 - name is 'H1' or 'H2'.
    The legacy 'T1/T2/T3' naming is preserved for back-compat fields."""
    name: str
    lots: float
    is_open: bool = True
    close_price: Optional[float] = None
    close_reason: Optional[CloseReason] = None
    close_time_utc: Optional[datetime] = None
    # Partial closes within this half (v3.2 RSI trim or COMEX exits)
    partial_closes: List[dict] = field(default_factory=list)


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
    accelerated_kind: Optional[str] = None      # 'WEDNESDAY' | 'STANDARD'
    rsi_trim_done: bool = False

    current_stop: float = 0.0
    trail_active: bool = False
    trail_distance: float = 0.0
    running_extreme: float = 0.0
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

    @property
    def half_1(self) -> Optional[TrancheState]:
        return next((t for t in self.tranches if t.name in ("H1", "T1")), None)

    @property
    def half_2(self) -> Optional[TrancheState]:
        return next((t for t in self.tranches if t.name in ("H2", "T2")), None)

    def overall_close_type(self) -> Optional[str]:
        """The summary close type for the trade. Used by Opt 2 for next day."""
        if self.tp2_hit:
            return "TP2"
        if self.tp1_hit:
            return "TP1"
        # All halves closed before TP1
        h1 = self.half_1
        if h1 is not None and h1.close_reason is not None:
            return h1.close_reason.value
        return None


@dataclass
class TickUpdate:
    closed: List[TrancheState] = field(default_factory=list)
    partial_closes: List[dict] = field(default_factory=list)
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


# ── Tick processing ──────────────────────────────────────
def apply_tick(
    ctx: PremarketContext,
    pos: PositionState,
    settings: Settings,
    tick_price: float,
    tick_time_utc: datetime,
) -> TickUpdate:
    update = TickUpdate()
    if pos.closed or pos.is_fully_closed:
        return update

    if tick_price > pos.intraday_high:
        pos.intraday_high = tick_price
    if tick_price < pos.intraday_low:
        pos.intraday_low = tick_price

    is_long = pos.direction == "LONG"

    # ── Stop-loss ────────────────────────────────────────
    sl_hit = (tick_price <= pos.current_stop) if is_long else (tick_price >= pos.current_stop)
    if sl_hit:
        if not pos.tp1_hit:
            reason = CloseReason.INITIAL_SL
        else:
            reason = CloseReason.TRAIL_STOP if pos.trail_active else CloseReason.INITIAL_SL
        for t in pos.open_tranches:
            update.closed.append(_close_tranche(t, pos.current_stop, reason, tick_time_utc))
        pos.closed = True
        return update

    # ── TP1 ──────────────────────────────────────────────
    if not pos.tp1_hit:
        target_tp1 = pos.accelerated_tp1_price if pos.accelerated_tp1 else pos.tp1
        tp1_hit = (tick_price >= target_tp1) if is_long else (tick_price <= target_tp1)
        if tp1_hit:
            pos.tp1_hit = True
            pos.tp1_hit_price = target_tp1
            pos.tp1_hit_time_utc = tick_time_utc
            h1 = pos.half_1
            if h1 is not None and h1.is_open:
                update.closed.append(_close_tranche(h1, target_tp1, CloseReason.TP1, tick_time_utc))
            pos.running_extreme = target_tp1

            # Breakeven-plus stop on H2
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

            # Trail
            pos.trail_distance = _compute_trail_distance(ctx, pos)
            pos.trail_active = pos.trail_distance > 0

            # RANGING regime override: close H2 at TP1 too
            if FLAGS.OPT_REGIME_DETECTOR and ctx.regime == "RANGING":
                h2 = pos.half_2
                if h2 is not None and h2.is_open:
                    update.closed.append(_close_tranche(
                        h2, target_tp1, CloseReason.REGIME_OVERRIDE, tick_time_utc))
                pos.closed = True
            return update

    # ── TP2 ──────────────────────────────────────────────
    if pos.tp1_hit and not pos.tp2_hit:
        tp2_hit = (tick_price >= pos.tp2) if is_long else (tick_price <= pos.tp2)
        if tp2_hit:
            pos.tp2_hit = True
            h2 = pos.half_2
            if h2 is not None and h2.is_open:
                update.closed.append(_close_tranche(h2, pos.tp2, CloseReason.TP2, tick_time_utc))
            pos.running_extreme = pos.tp2

    # ── Trail update on still-open H2 ────────────────────
    if pos.tp1_hit and pos.trail_active and not pos.is_fully_closed:
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


# ── TP1 acceleration (15:30 NY + Wednesday 14:30 NY) ─────
def maybe_accelerate_tp1(
    ctx: PremarketContext,
    pos: PositionState,
    settings: Settings,
    now_utc: datetime,
) -> bool:
    if pos.closed or pos.tp1_hit or pos.accelerated_tp1:
        return False

    # v3.2 Opt 5: Wednesday 14:30 NY (70% of original distance)
    if (FLAGS.OPT_5_WEDNESDAY_ACCEL and session.is_wednesday(ctx.trade_date)
            and not pos.accelerated_tp1):
        trigger_utc = session.ny_time_to_utc(ctx.trade_date, 14, 30)
        if now_utc >= trigger_utc:
            original = abs(pos.tp1 - pos.entry_price)
            new_dist = original * 0.70
            pos.accelerated_tp1_price = (pos.entry_price + new_dist
                                          if pos.direction == "LONG"
                                          else pos.entry_price - new_dist)
            pos.accelerated_tp1 = True
            pos.accelerated_kind = "WEDNESDAY"
            return True

    # Standard: 15:30 NY (80% of original distance)
    if not FLAGS.OPT_TP1_TIME_ACCEL:
        return False
    trigger_utc = session.ny_time_to_utc(ctx.trade_date, 15, 30)
    if now_utc < trigger_utc:
        return False
    original = abs(pos.tp1 - pos.entry_price)
    new_dist = original * settings.tp1_accel_pct
    pos.accelerated_tp1_price = (pos.entry_price + new_dist
                                  if pos.direction == "LONG"
                                  else pos.entry_price - new_dist)
    pos.accelerated_tp1 = True
    pos.accelerated_kind = "STANDARD"
    return True


# ── Opt 6: RSI post-TP1 trim ─────────────────────────────
def maybe_rsi_trim(
    pos: PositionState,
    closes_15m_post_tp1: Sequence[float],
    settings: Settings,
    now_utc: datetime,
) -> Optional[dict]:
    """Called once on the first 15-min candle close AFTER TP1 is hit.
    Returns a dict describing the trim if triggered, else None."""
    if not FLAGS.OPT_6_RSI_POST_TP1_TRIM:
        return None
    if not pos.tp1_hit or pos.rsi_trim_done or pos.closed:
        return None
    if len(closes_15m_post_tp1) < 15:
        return None
    h2 = pos.half_2
    if h2 is None or not h2.is_open:
        pos.rsi_trim_done = True
        return None

    rsi_val = indicators.rsi(closes_15m_post_tp1, 14)
    is_long = pos.direction == "LONG"
    trigger = (is_long and rsi_val > 72.0) or (not is_long and rsi_val < 28.0)
    pos.rsi_trim_done = True   # one-shot regardless

    if not trigger:
        return None

    # Trim 25% of remaining H2 lots
    import math
    trim_lots = max(math.floor(h2.lots * 0.25 / settings.lot_step) * settings.lot_step,
                     settings.lot_step)
    trim_lots = min(trim_lots, h2.lots)
    h2.lots = round(h2.lots - trim_lots, 4)
    record = {
        "trim_lots": trim_lots,
        "rsi_at_trim": round(rsi_val, 2),
        "time_utc": now_utc,
        "remaining_h2_lots": h2.lots,
    }
    h2.partial_closes.append({"reason": CloseReason.RSI_TRIM.value, **record})
    return record


# ── Opt 1: COMEX volume fade exit ─────────────────────────
def maybe_comex_volume_exit(
    pos: PositionState,
    tracker: Optional[ComexVolumeTracker],
    now_utc: datetime,
) -> Optional[TrancheState]:
    """If COMEX feed reports 2 consecutive 15-min bars under 50% avg volume,
    exit H2 at market. Returns the closed tranche, or None if not triggered."""
    if not FLAGS.OPT_1_COMEX_VOL_CONTINUATION:
        return None
    if tracker is None or tracker.is_stale(now_utc):
        return None
    if not pos.tp1_hit or pos.tp2_hit or pos.closed:
        return None
    if tracker.consecutive_weak < tracker.consecutive_to_exit:
        return None
    h2 = pos.half_2
    if h2 is None or not h2.is_open:
        return None
    return h2   # caller does the actual close at current market price


# ── 20:55 UTC partial-close decision tree ────────────────
def maybe_2055_force_close(
    ctx: PremarketContext,
    pos: PositionState,
    settings: Settings,
    last_price: float,
    now_utc: datetime,
) -> List[TrancheState]:
    """At 20:55 UTC, apply the v3.1 §6 decision tree. Returns tranches closed."""
    if not FLAGS.OPT_SESSION_FORCE_CLOSE_2055:
        return []
    trigger = session.force_close_2055_utc(ctx.trade_date)
    if now_utc < trigger or pos.closed:
        return []

    closed: List[TrancheState] = []
    if not pos.tp1_hit:
        for t in pos.open_tranches:
            closed.append(_close_tranche(t, last_price,
                                          CloseReason.SESSION_CLOSE_FULL_NO_TP1, now_utc))
        pos.closed = True
        return closed

    if pos.tp1_hit and not pos.tp2_hit:
        h2 = pos.half_2
        if h2 is not None and h2.is_open:
            closed.append(_close_tranche(h2, last_price,
                                          CloseReason.SESSION_CLOSE_HALF2, now_utc))
        pos.closed = True
        return closed

    # TP2 hit but somehow tranche still open (defensive)
    for t in pos.open_tranches:
        closed.append(_close_tranche(t, last_price,
                                      CloseReason.SESSION_CLOSE_RESIDUAL, now_utc))
    pos.closed = True
    return closed


# ── 21:00 UTC absolute safety net ────────────────────────
def force_close_session_end(
    pos: PositionState,
    last_price: float,
    now_utc: datetime,
) -> List[TrancheState]:
    closed: List[TrancheState] = []
    for t in pos.open_tranches:
        closed.append(_close_tranche(t, last_price, CloseReason.SESSION_END, now_utc))
    pos.closed = True
    return closed

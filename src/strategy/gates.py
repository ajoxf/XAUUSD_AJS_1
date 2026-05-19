"""Pre-trade gates — spec §3 v3.2. Every gate must pass before signal accepted."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import List

from config.flags import FLAGS
from config.settings import Settings
from src.strategy.premarket import PremarketContext


@dataclass(frozen=True)
class GateResult:
    passed: bool
    failures: List[str]


def _check_g1_atr(ctx: PremarketContext, settings: Settings) -> List[str]:
    fails: List[str] = []
    if not FLAGS.FILTER_A_ATR_RANGE:
        return fails
    if ctx.range < ctx.atr_20:
        fails.append(f"G1: range {ctx.range:.2f} < ATR20 {ctx.atr_20:.2f}")
    cap = settings.atr_cap_multiplier
    if FLAGS.FILTER_A_ATR_CAP and ctx.range > cap * ctx.atr_20:
        fails.append(f"G1: range {ctx.range:.2f} > {cap}×ATR20 {cap * ctx.atr_20:.2f}")
    if FLAGS.FIX_L3_ATR_CAP and ctx.range > cap * ctx.range_10d_median:
        fails.append(
            f"G1: range {ctx.range:.2f} > {cap}×median10 {cap * ctx.range_10d_median:.2f}"
        )
    return fails


def _check_g2_session(ctx: PremarketContext, now_utc: datetime) -> List[str]:
    if not FLAGS.FILTER_B_SESSION_DST:
        return []
    if not (ctx.session_start_utc <= now_utc <= ctx.session_end_utc):
        return [f"G2: {now_utc.isoformat()} outside session "
                f"[{ctx.session_start_utc.isoformat()}, {ctx.session_end_utc.isoformat()}]"]
    return []


def _check_g3_events(ctx: PremarketContext) -> List[str]:
    if not FLAGS.FILTER_E_EVENT_AVOID:
        return []
    if ctx.event_blocked:
        return [f"G3: {ctx.event_reason}"]
    return []


def _check_g4_daily_lock(daily_fired: bool, position_open: bool) -> List[str]:
    if not FLAGS.FIX_L2_DAILY_LOCK:
        return []
    fails: List[str] = []
    if daily_fired:
        fails.append("G4: daily trade lock fired")
    if position_open:
        fails.append("G4: position already open")
    return fails


def _check_g5_trend(ctx: PremarketContext, direction: str) -> List[str]:
    if not FLAGS.OPT_STEP2_4H_TREND_HARD:
        return []
    bias = ctx.trend_bias
    if bias == "BOTH":
        return []
    if direction == "LONG" and bias != "LONG_ONLY":
        return [f"G5: {direction} blocked by trend_bias={bias}"]
    if direction == "SHORT" and bias != "SHORT_ONLY":
        return [f"G5: {direction} blocked by trend_bias={bias}"]
    return []


def _check_g6_sma200_short(ctx: PremarketContext, direction: str) -> List[str]:
    """v3.2 Opt 4: block shorts when price is above the 200-day SMA."""
    if not FLAGS.OPT_4_SMA200_SHORT_FILTER:
        return []
    if direction != "SHORT":
        return []
    if ctx.prev_close > ctx.sma200_daily:
        return [f"G6: short blocked — price {ctx.prev_close:.2f} > "
                f"SMA200 {ctx.sma200_daily:.2f}"]
    return []


def _check_g7_floor(ctx: PremarketContext, settings: Settings) -> List[str]:
    if ctx.range < settings.min_range_floor:
        return [f"G7: range {ctx.range:.2f} < floor {settings.min_range_floor}"]
    return []


def check_all_gates(
    ctx: PremarketContext,
    settings: Settings,
    now_utc: datetime,
    direction: str,
    daily_fired: bool,
    position_open: bool,
) -> GateResult:
    failures: List[str] = []
    failures += _check_g1_atr(ctx, settings)
    failures += _check_g2_session(ctx, now_utc)
    failures += _check_g3_events(ctx)
    failures += _check_g4_daily_lock(daily_fired, position_open)
    failures += _check_g5_trend(ctx, direction)
    failures += _check_g6_sma200_short(ctx, direction)
    failures += _check_g7_floor(ctx, settings)
    return GateResult(passed=(len(failures) == 0), failures=failures)

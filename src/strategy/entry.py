"""Four-layer entry confirmation - spec §4.

Layer A: level cross (caller monitors price stream)
Layer B: 15m candle close + body >= 60% of range on correct side
Layer C: RSI(14) on 15m in neutral zone for direction
Layer D: re-test confirmation (next 15m candle: continuation or valid pullback)
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional, Sequence

from config.flags import FLAGS
from config.settings import Settings
from src.strategy import indicators
from src.strategy.premarket import PremarketContext


@dataclass(frozen=True)
class Candle:
    open: float
    high: float
    low: float
    close: float


class Direction(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"


@dataclass(frozen=True)
class LayerResult:
    passed: bool
    reasons: List[str]


def _body_ratio(c: Candle) -> float:
    rng = c.high - c.low
    if rng <= 0:
        return 0.0
    return abs(c.close - c.open) / rng


def layer_b_body_close(
    candle: Candle, direction: Direction, level: float, settings: Settings
) -> LayerResult:
    if not FLAGS.FIX_L1_CANDLE_CONFIRM:
        return LayerResult(True, [])
    threshold = settings.body_ratio_threshold if FLAGS.OPT_STEP1_BODY_60PCT else 0.50
    body = _body_ratio(candle)
    reasons: List[str] = []
    if direction == Direction.LONG:
        if candle.close <= level:
            reasons.append(f"B: close {candle.close} <= level {level}")
    else:
        if candle.close >= level:
            reasons.append(f"B: close {candle.close} >= level {level}")
    if body < threshold:
        reasons.append(f"B: body {body:.2f} < {threshold}")
    return LayerResult(len(reasons) == 0, reasons)


def layer_c_rsi(
    closes_15m: Sequence[float], direction: Direction, settings: Settings
) -> LayerResult:
    if not FLAGS.OPT_STEP3_RSI_ZONE:
        return LayerResult(True, [])
    rsi_val = indicators.rsi(closes_15m, 14)
    if direction == Direction.LONG:
        lo, hi = settings.rsi_long_min, settings.rsi_long_max
    else:
        lo, hi = settings.rsi_short_min, settings.rsi_short_max
    if lo <= rsi_val <= hi:
        return LayerResult(True, [])
    return LayerResult(False, [f"C: RSI {rsi_val:.1f} outside [{lo}, {hi}]"])


def retest_tolerance(range_: float, settings: Settings) -> float:
    return max(2.0, range_ * 0.027)


@dataclass(frozen=True)
class RetestVerdict:
    passed: bool
    entry_kind: str           # 'CONTINUATION' | 'RETEST' | 'FAIL'
    reasons: List[str]


def layer_d_retest(
    confirmation_candle: Candle,   # the candle that passed Layer B
    next_candle: Candle,            # the next 15m candle (must close)
    direction: Direction,
    level: float,
    range_: float,
    settings: Settings,
) -> RetestVerdict:
    if not FLAGS.OPT_STEP4_RETEST_CONFIRM:
        return RetestVerdict(True, "CONTINUATION", [])

    tol = retest_tolerance(range_, settings)

    if direction == Direction.LONG:
        # Immediate invalidation
        if next_candle.close < level:
            return RetestVerdict(False, "FAIL", [
                f"D: next close {next_candle.close} broke back below level {level}"])
        # Pullback to level → must close back above
        if next_candle.low <= level + tol:
            if next_candle.close > level:
                return RetestVerdict(True, "RETEST", [])
            return RetestVerdict(False, "FAIL", [
                f"D: retest at level failed - close {next_candle.close} <= level"])
        # Continuation
        if next_candle.low > level - tol:
            return RetestVerdict(True, "CONTINUATION", [])
        return RetestVerdict(False, "FAIL", ["D: ambiguous continuation"])

    # SHORT
    if next_candle.close > level:
        return RetestVerdict(False, "FAIL", [
            f"D: next close {next_candle.close} broke back above level {level}"])
    if next_candle.high >= level - tol:
        if next_candle.close < level:
            return RetestVerdict(True, "RETEST", [])
        return RetestVerdict(False, "FAIL", [
            f"D: retest at level failed - close {next_candle.close} >= level"])
    if next_candle.high < level + tol:
        return RetestVerdict(True, "CONTINUATION", [])
    return RetestVerdict(False, "FAIL", ["D: ambiguous continuation"])


@dataclass(frozen=True)
class EntryEvaluation:
    accepted: bool
    direction: Direction
    layer_b: LayerResult
    layer_c: LayerResult
    layer_d: Optional[RetestVerdict]
    entry_kind: Optional[str]


def evaluate_entry(
    ctx: PremarketContext,
    direction: Direction,
    confirmation_candle: Candle,
    next_candle: Optional[Candle],
    closes_15m: Sequence[float],
    settings: Settings,
) -> EntryEvaluation:
    """Run B → C → D. Returns full evaluation. Caller is responsible for
    Layer A (level cross) and for waiting for `next_candle` to close before
    invoking Layer D."""
    level = ctx.long_entry if direction == Direction.LONG else ctx.short_entry

    b = layer_b_body_close(confirmation_candle, direction, level, settings)
    if not b.passed:
        return EntryEvaluation(False, direction, b, LayerResult(False, []), None, None)

    c = layer_c_rsi(closes_15m, direction, settings)
    if not c.passed:
        return EntryEvaluation(False, direction, b, c, None, None)

    if next_candle is None:
        # Caller hasn't waited for next candle yet - defer
        return EntryEvaluation(False, direction, b, c, None, "PENDING_RETEST")

    d = layer_d_retest(confirmation_candle, next_candle, direction,
                       level, ctx.range, settings)
    return EntryEvaluation(d.passed, direction, b, c, d, d.entry_kind)

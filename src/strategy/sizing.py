"""Position sizing — spec §5. 3% base risk + seasonal + alignment + regime."""
from __future__ import annotations

import math
from dataclasses import dataclass

from config.flags import FLAGS
from config.settings import Settings
from src.strategy.premarket import PremarketContext


@dataclass(frozen=True)
class SizingResult:
    direction: str
    entry_price: float
    stop_loss: float
    sl_distance: float
    risk_amount: float

    lots_raw: float
    lots_base: float
    seasonal_mult: float
    alignment_mult: float
    regime_mult: float
    lots_final: float

    tranche_1: float
    tranche_2: float
    tranche_3: float

    actual_risk: float
    deviation_pct: float
    warning: bool


def _floor_to_step(value: float, step: float) -> float:
    if step <= 0:
        return value
    return math.floor(value / step) * step


def compute_size(
    ctx: PremarketContext,
    settings: Settings,
    direction: str,
    entry_price: float,
    current_equity: float,
    risk_pct_override: float | None = None,
) -> SizingResult:
    risk_pct = risk_pct_override if risk_pct_override is not None else settings.risk_pct
    risk_amount = current_equity * risk_pct

    sl = ctx.long_sl if direction == "LONG" else ctx.short_sl
    sl_distance = abs(entry_price - sl)
    if sl_distance <= 0:
        raise ValueError("sl_distance must be positive")

    position_oz = risk_amount / sl_distance
    lots_raw = position_oz / settings.contract_size
    lots_base = max(_floor_to_step(lots_raw, settings.lot_step), settings.lot_step)

    if FLAGS.OPT_SEASONAL_SIZING and direction == "LONG":
        seasonal_mult = ctx.seasonal_mult_long
    else:
        seasonal_mult = 1.0

    alignment_mult = 0.50 if (FLAGS.OPT_STEP2_4H_TREND_HARD and ctx.trend_bias == "BOTH") else 1.0

    if FLAGS.OPT_REGIME_DETECTOR and ctx.regime == "RANGING":
        regime_mult = 0.80
    else:
        regime_mult = 1.0

    lots_final = max(
        _floor_to_step(lots_base * seasonal_mult * alignment_mult * regime_mult,
                       settings.lot_step),
        settings.lot_step,
    )

    if FLAGS.OPT_THREE_TRANCHE_EXIT:
        t1 = max(_floor_to_step(lots_final * settings.tranche_1_pct, settings.lot_step),
                 settings.lot_step)
        t2 = max(_floor_to_step(lots_final * settings.tranche_2_pct, settings.lot_step),
                 settings.lot_step)
        t3 = round(lots_final - t1 - t2, 4)
        if t3 < settings.lot_step:
            # Tiny final position — fold residual into t1
            t1 = round(t1 + t3, 4) if t3 > 0 else t1
            t3 = 0.0
    else:
        t1 = max(_floor_to_step(lots_final * 0.50, settings.lot_step), settings.lot_step)
        t2 = round(lots_final - t1, 4)
        t3 = 0.0

    actual_risk = lots_final * settings.contract_size * sl_distance
    deviation_pct = abs(actual_risk - risk_amount) / risk_amount * 100.0
    warning = FLAGS.FIX_L8_ROUNDING_AUDIT and (deviation_pct > 5.0)

    return SizingResult(
        direction=direction,
        entry_price=entry_price,
        stop_loss=sl,
        sl_distance=sl_distance,
        risk_amount=risk_amount,
        lots_raw=lots_raw,
        lots_base=lots_base,
        seasonal_mult=seasonal_mult,
        alignment_mult=alignment_mult,
        regime_mult=regime_mult,
        lots_final=lots_final,
        tranche_1=t1,
        tranche_2=t2,
        tranche_3=t3,
        actual_risk=actual_risk,
        deviation_pct=deviation_pct,
        warning=warning,
    )

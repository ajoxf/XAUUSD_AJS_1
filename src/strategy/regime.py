"""Volatility regime classifier - spec §2."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Regime = Literal["TRENDING", "RANGING", "NEUTRAL"]


@dataclass(frozen=True)
class RegimeReading:
    regime: Regime
    ratio: float


def classify_regime(atr_20: float, atr_50: float) -> RegimeReading:
    if atr_50 <= 0:
        raise ValueError("atr_50 must be positive")
    ratio = atr_20 / atr_50
    if ratio > 1.15:
        return RegimeReading("TRENDING", ratio)
    if ratio < 0.85:
        return RegimeReading("RANGING", ratio)
    return RegimeReading("NEUTRAL", ratio)

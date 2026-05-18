"""Pure technical indicators. No I/O, all numpy/pandas."""
from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd


def true_range(high: pd.Series, low: pd.Series, prev_close: pd.Series) -> pd.Series:
    hl = high - low
    hc = (high - prev_close).abs()
    lc = (low - prev_close).abs()
    return pd.concat([hl, hc, lc], axis=1).max(axis=1)


def atr(df: pd.DataFrame, period: int) -> float:
    """ATR over the last `period` completed bars. df cols: high, low, close."""
    if len(df) < period + 1:
        raise ValueError(f"Need at least {period + 1} bars for ATR({period}), got {len(df)}")
    prev_close = df["close"].shift(1)
    tr = true_range(df["high"], df["low"], prev_close).dropna()
    return float(tr.tail(period).mean())


def ema(values: Sequence[float], period: int) -> float:
    """Final EMA value over `values`, computed as pandas would."""
    s = pd.Series(values, dtype=float)
    if len(s) < period:
        raise ValueError(f"Need at least {period} values for EMA({period}), got {len(s)}")
    return float(s.ewm(span=period, adjust=False).mean().iloc[-1])


def ema_series(values: Sequence[float], period: int) -> pd.Series:
    return pd.Series(values, dtype=float).ewm(span=period, adjust=False).mean()


def rsi(closes: Sequence[float], period: int = 14) -> float:
    """Wilder's RSI, final value."""
    s = pd.Series(closes, dtype=float)
    if len(s) < period + 1:
        raise ValueError(f"Need at least {period + 1} closes for RSI({period}), got {len(s)}")
    delta = s.diff()
    gain = delta.clip(lower=0.0)
    loss = (-delta).clip(lower=0.0)
    avg_gain = gain.ewm(alpha=1.0 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1.0 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100.0 - (100.0 / (1.0 + rs))
    val = out.iloc[-1]
    if pd.isna(val):
        return 100.0   # no losses → max RSI
    return float(val)


def median_range(df: pd.DataFrame, lookback: int) -> float:
    if len(df) < lookback:
        raise ValueError(f"Need at least {lookback} bars for median range, got {len(df)}")
    ranges = (df["high"] - df["low"]).tail(lookback)
    return float(ranges.median())

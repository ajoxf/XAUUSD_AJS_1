"""Pre-market calculation — spec §2. Runs once daily at 00:05 UTC.

Produces a frozen `PremarketContext` carrying every value the intraday loop
needs. Nothing in §2 is recomputed during the session.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from typing import Literal, Optional, Tuple

import pandas as pd

from config.flags import FLAGS
from src.strategy import events, indicators, seasonal, session
from src.strategy.regime import Regime, RegimeReading, classify_regime

TrendBias = Literal["LONG_ONLY", "SHORT_ONLY", "BOTH"]


@dataclass(frozen=True)
class PremarketContext:
    trade_date: date

    # OHLC reference
    prev_open: float
    prev_high: float
    prev_low: float
    prev_close: float
    range: float

    # Indicators
    atr_20: float
    atr_50: float
    range_10d_median: float
    ema50_4h: float
    ema5_daily_now: float
    ema5_daily_prev: float

    # Regime
    regime: Regime
    regime_ratio: float

    # Trend bias
    trend_bias: TrendBias

    # Fibonacci levels
    long_entry: float
    short_entry: float
    long_sl: float
    short_sl: float
    long_tp1: float
    short_tp1: float
    long_tp2: float
    short_tp2: float

    # Filter flags
    filter_c_active: bool       # low-ATR day → dynamic TP1
    filter_f_suppressed: bool   # if C active, F suppressed
    long_filter_f: bool         # trend-confirming long → extended TP2
    short_filter_f: bool        # trend-confirming short → extended TP2

    # Trail
    trail_distance_base: float

    # Session
    session_start_utc: datetime
    session_end_utc: datetime

    # Event blocks
    event_blocked: bool
    event_reason: Optional[str]

    # Seasonal
    seasonal_mult_long: float


def _trend_bias(prev_close: float, ema50_4h: float, tolerance_pct: float = 0.002) -> TrendBias:
    band = ema50_4h * tolerance_pct
    if prev_close > ema50_4h + band:
        return "LONG_ONLY"
    if prev_close < ema50_4h - band:
        return "SHORT_ONLY"
    return "BOTH"


def _trend_confirm(prev_open: float, prev_close: float,
                   ema5_now: float, ema5_prev: float,
                   range_: float, atr_20: float, direction: str) -> bool:
    if range_ < atr_20 or range_ < 20.0:
        return False
    if direction == "LONG":
        return (prev_close > prev_open) and (ema5_now > ema5_prev)
    return (prev_close < prev_open) and (ema5_now < ema5_prev)


def build_premarket(
    trade_date: date,
    daily_df: pd.DataFrame,       # cols: open, high, low, close. Last row = previous completed day.
    h4_closes: pd.Series,         # 4h closes through pre-session
) -> PremarketContext:
    """All required history must already be in `daily_df` (≥51 bars) and
    `h4_closes` (≥50 values). Indexed by datetime ascending."""
    if len(daily_df) < 51:
        raise ValueError(f"daily_df needs ≥51 rows for ATR(50), got {len(daily_df)}")
    if len(h4_closes) < 50:
        raise ValueError(f"h4_closes needs ≥50 rows for EMA(50) on 4H, got {len(h4_closes)}")

    prev = daily_df.iloc[-1]
    prev_open = float(prev["open"])
    prev_high = float(prev["high"])
    prev_low = float(prev["low"])
    prev_close = float(prev["close"])

    if not (prev_high >= prev_low):
        raise ValueError("Invalid range: prev_high < prev_low")
    if not (prev_low <= prev_close <= prev_high):
        raise ValueError("Close outside prior day range")
    range_ = prev_high - prev_low
    if range_ <= 0:
        raise ValueError("Zero range")

    atr_20 = indicators.atr(daily_df.tail(21), 20)
    atr_50 = indicators.atr(daily_df.tail(51), 50)
    range_10d_median = indicators.median_range(daily_df.tail(10), 10)
    ema50_4h = indicators.ema(h4_closes.tolist(), 50)

    ema5_series = indicators.ema_series(daily_df["close"].tolist(), 5)
    ema5_now = float(ema5_series.iloc[-1])
    ema5_prev = float(ema5_series.iloc[-2])

    regime_reading: RegimeReading = (
        classify_regime(atr_20, atr_50) if FLAGS.OPT_REGIME_DETECTOR
        else RegimeReading("NEUTRAL", atr_20 / atr_50)
    )

    trend_bias = (
        _trend_bias(prev_close, ema50_4h) if FLAGS.OPT_STEP2_4H_TREND_HARD
        else "BOTH"
    )

    long_entry = prev_close + 0.382 * range_
    short_entry = prev_close - 0.382 * range_
    long_sl = prev_close
    short_sl = prev_close

    filter_c_active = (range_ < atr_20) if FLAGS.FILTER_C_DYNAMIC_TP1 else False
    if filter_c_active:
        long_tp1 = prev_close + 0.500 * range_
        short_tp1 = prev_close - 0.500 * range_
        filter_f_suppressed = FLAGS.FILTER_CF_MUTUAL_EXCLUSION
    else:
        long_tp1 = prev_close + 0.618 * range_
        short_tp1 = prev_close - 0.618 * range_
        filter_f_suppressed = False

    long_filter_f = (FLAGS.FILTER_F_EXTENDED_TP2 and not filter_f_suppressed
                     and _trend_confirm(prev_open, prev_close, ema5_now, ema5_prev,
                                        range_, atr_20, "LONG"))
    short_filter_f = (FLAGS.FILTER_F_EXTENDED_TP2 and not filter_f_suppressed
                      and _trend_confirm(prev_open, prev_close, ema5_now, ema5_prev,
                                         range_, atr_20, "SHORT"))

    long_tp2 = prev_close + (1.272 if long_filter_f else 1.000) * range_
    short_tp2 = prev_close - (1.272 if short_filter_f else 1.000) * range_

    trail_distance_base = 0.382 * range_

    start_utc, end_utc = session.session_window_utc(trade_date)

    blocked, reason = (events.is_blocked_day(trade_date)
                       if FLAGS.FILTER_E_EVENT_AVOID else (False, None))

    seasonal_mult = seasonal.seasonal_multiplier(trade_date.month, "LONG") \
        if FLAGS.OPT_SEASONAL_SIZING else 1.0

    return PremarketContext(
        trade_date=trade_date,
        prev_open=prev_open, prev_high=prev_high, prev_low=prev_low, prev_close=prev_close,
        range=range_,
        atr_20=atr_20, atr_50=atr_50, range_10d_median=range_10d_median,
        ema50_4h=ema50_4h, ema5_daily_now=ema5_now, ema5_daily_prev=ema5_prev,
        regime=regime_reading.regime, regime_ratio=regime_reading.ratio,
        trend_bias=trend_bias,
        long_entry=long_entry, short_entry=short_entry,
        long_sl=long_sl, short_sl=short_sl,
        long_tp1=long_tp1, short_tp1=short_tp1,
        long_tp2=long_tp2, short_tp2=short_tp2,
        filter_c_active=filter_c_active, filter_f_suppressed=filter_f_suppressed,
        long_filter_f=long_filter_f, short_filter_f=short_filter_f,
        trail_distance_base=trail_distance_base,
        session_start_utc=start_utc, session_end_utc=end_utc,
        event_blocked=blocked, event_reason=reason,
        seasonal_mult_long=seasonal_mult,
    )

"""yfinance-backed DataFeed. XAUUSD ticker = 'GC=F' (Gold futures, COMEX).

yfinance is the chosen historical/backfill source. For live execution, MT5
streams data via the broker; yfinance is only used for pre-market history.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

import pandas as pd

try:
    import yfinance as yf
except ImportError:
    yf = None


_DEFAULT_TICKER = "GC=F"


def _normalise_columns(df: pd.DataFrame) -> pd.DataFrame:
    out = df.rename(columns=str.lower)
    keep = [c for c in ("open", "high", "low", "close") if c in out.columns]
    return out[keep].dropna()


class YFinanceFeed:
    def __init__(self, ticker: str = _DEFAULT_TICKER):
        if yf is None:
            raise ImportError("yfinance not installed - `pip install yfinance`")
        self.ticker = ticker

    def daily(self, symbol: str, end: date, lookback_days: int) -> pd.DataFrame:
        # Fetch with cushion for weekends/holidays
        start = end - timedelta(days=lookback_days * 2 + 14)
        df = yf.download(self.ticker, start=start, end=end + timedelta(days=1),
                         interval="1d", progress=False, auto_adjust=False)
        if df.empty:
            raise RuntimeError(f"yfinance returned no daily data for {self.ticker}")
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return _normalise_columns(df).tail(lookback_days)

    def h4(self, symbol: str, end_utc: datetime, lookback_bars: int) -> pd.DataFrame:
        # yfinance only offers 1h for futures; resample to 4h
        start = end_utc - timedelta(days=int(lookback_bars / 6) + 30)
        df = yf.download(self.ticker, start=start, end=end_utc,
                         interval="1h", progress=False, auto_adjust=False)
        if df.empty:
            raise RuntimeError(f"yfinance returned no 1h data for {self.ticker}")
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = _normalise_columns(df)
        agg = {"open": "first", "high": "max", "low": "min", "close": "last"}
        h4 = df.resample("4h").agg(agg).dropna()
        return h4.tail(lookback_bars)

    def m15(self, symbol: str, start_utc: datetime, end_utc: datetime) -> pd.DataFrame:
        df = yf.download(self.ticker, start=start_utc, end=end_utc,
                         interval="15m", progress=False, auto_adjust=False)
        if df.empty:
            raise RuntimeError(f"yfinance returned no 15m data for {self.ticker}")
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return _normalise_columns(df)

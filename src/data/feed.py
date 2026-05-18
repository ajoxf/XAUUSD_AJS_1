"""DataFeed protocol — abstracts historical/intraday OHLC retrieval."""
from __future__ import annotations

from datetime import date, datetime
from typing import Protocol

import pandas as pd


class DataFeed(Protocol):
    def daily(self, symbol: str, end: date, lookback_days: int) -> pd.DataFrame:
        """Return DataFrame indexed by date, cols: open, high, low, close.
        Last row must be the completed bar for `end - 1 trading day` or earlier."""
        ...

    def h4(self, symbol: str, end_utc: datetime, lookback_bars: int) -> pd.DataFrame:
        """4-hour OHLC ending at or before `end_utc`. Cols: open, high, low, close."""
        ...

    def m15(self, symbol: str, start_utc: datetime, end_utc: datetime) -> pd.DataFrame:
        """15-minute OHLC for the given window."""
        ...

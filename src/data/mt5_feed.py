"""MT5-backed DataFeed.

Pulls daily / 4-hour / 15-minute OHLC straight from the connected MT5
terminal, so every strategy level (Fibonacci entries, ATR, SMA200, EMA)
is computed from the SAME instrument the bot trades. This is critical:
yfinance's gold ticker (GC=F) is COMEX *futures*, which differ from the
broker's *spot* XAUUSD by the basis - using it would mis-place every level.

Requires MT5 to be initialised (the broker adapter does this on connect).
"""
from __future__ import annotations

from datetime import date, datetime

import pandas as pd

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None


class MT5DataFeed:
    def __init__(self, symbol: str):
        if mt5 is None:
            raise ImportError("MetaTrader5 package not available - MT5DataFeed "
                              "only works on a host with the MT5 terminal.")
        self.symbol = symbol

    def _rates(self, timeframe: int, count: int, start_pos: int) -> pd.DataFrame:
        rates = mt5.copy_rates_from_pos(self.symbol, timeframe, start_pos, count)
        if rates is None or len(rates) == 0:
            raise RuntimeError(
                f"MT5 returned no rates for {self.symbol} (tf={timeframe}): "
                f"{mt5.last_error()}. Check the symbol is in Market Watch and "
                "the chart timeframe has history loaded."
            )
        df = pd.DataFrame(rates)
        # MT5 'time' is the bar-open epoch in the broker's server timezone.
        # We label it UTC for a stable index; absolute OHLC values are
        # unaffected, and the session window is computed separately via pytz.
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.set_index("time")
        return df[["open", "high", "low", "close"]]

    def daily(self, symbol: str, end: date, lookback_days: int) -> pd.DataFrame:
        # start_pos=1 skips today's still-forming D1 bar so the last row is
        # the most recent COMPLETED day (= "previous day" in the spec).
        return self._rates(mt5.TIMEFRAME_D1, lookback_days, start_pos=1).tail(lookback_days)

    def h4(self, symbol: str, end_utc: datetime, lookback_bars: int) -> pd.DataFrame:
        return self._rates(mt5.TIMEFRAME_H4, lookback_bars, start_pos=1).tail(lookback_bars)

    def m15(self, symbol: str, start_utc: datetime, end_utc: datetime) -> pd.DataFrame:
        # Most recent completed 15m bars (used for RSI at signal time).
        return self._rates(mt5.TIMEFRAME_M15, 300, start_pos=0)

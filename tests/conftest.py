"""Shared pytest fixtures."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from config.settings import Settings
from src.broker.paper_adapter import PaperAdapter


@pytest.fixture
def settings() -> Settings:
    return Settings(
        mt5_login=0, mt5_password="", mt5_server="", mt5_terminal_path="",
        symbol="XAUUSD", starting_equity=100_000.0, risk_pct=0.03,
        magic_number=20260101,
        mode="paper", log_level="INFO", log_dir=Path("/tmp/xauusd_test_logs"),
    )


@pytest.fixture
def paper_broker(settings: Settings) -> PaperAdapter:
    broker = PaperAdapter(starting_equity=settings.starting_equity, spread=0.20)
    broker.set_quote(mid=2000.0, time_utc=datetime(2024, 6, 14, 13, 30, tzinfo=timezone.utc))
    return broker


def synthetic_daily(end_date: date, n: int = 60,
                    base_price: float = 2000.0,
                    daily_range: float = 25.0,
                    drift: float = 0.0,
                    seed: int = 42) -> pd.DataFrame:
    """Generates n consecutive trading days of OHLC ending on end_date - 1."""
    rng = np.random.default_rng(seed)
    rows = []
    price = base_price
    for i in range(n):
        d = end_date - timedelta(days=n - i)
        rng_today = daily_range + rng.normal(0, 3)
        open_ = price + rng.normal(0, 2)
        high = open_ + abs(rng.normal(0, rng_today / 2))
        low = open_ - abs(rng.normal(0, rng_today / 2))
        close = open_ + drift + rng.normal(0, rng_today / 4)
        close = min(max(close, low), high)
        rows.append({"date": d, "open": open_, "high": high,
                     "low": low, "close": close})
        price = close
    df = pd.DataFrame(rows).set_index("date")
    return df


def synthetic_h4(end_utc: datetime, n: int = 60, start_price: float = 2000.0,
                 seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    price = start_price
    for i in range(n):
        ts = end_utc - timedelta(hours=4 * (n - i))
        open_ = price
        move = rng.normal(0, 3)
        close = price + move
        high = max(open_, close) + abs(rng.normal(0, 1))
        low = min(open_, close) - abs(rng.normal(0, 1))
        rows.append({"ts": ts, "open": open_, "high": high,
                     "low": low, "close": close})
        price = close
    return pd.DataFrame(rows).set_index("ts")


@pytest.fixture
def daily_df():
    return synthetic_daily(date(2024, 6, 14))


@pytest.fixture
def h4_df():
    return synthetic_h4(datetime(2024, 6, 14, 13, 30, tzinfo=timezone.utc))

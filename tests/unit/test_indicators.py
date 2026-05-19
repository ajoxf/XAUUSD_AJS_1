import numpy as np
import pandas as pd
import pytest

from src.strategy import indicators


def test_atr_constant_range():
    df = pd.DataFrame({
        "high": [10.0] * 25,
        "low": [5.0] * 25,
        "close": [7.5] * 25,
    })
    val = indicators.atr(df, 20)
    assert val == pytest.approx(5.0, rel=1e-9)


def test_atr_insufficient_history():
    df = pd.DataFrame({"high": [1.0] * 5, "low": [0.0] * 5, "close": [0.5] * 5})
    with pytest.raises(ValueError):
        indicators.atr(df, 20)


def test_ema_known_value():
    # Final EMA(3) of [1,2,3,4,5] with adjust=False = expected pandas value
    expected = pd.Series([1, 2, 3, 4, 5], dtype=float).ewm(span=3, adjust=False).mean().iloc[-1]
    assert indicators.ema([1, 2, 3, 4, 5], 3) == pytest.approx(float(expected))


def test_rsi_constant_rising():
    closes = list(range(1, 30))   # strict monotonic increase
    val = indicators.rsi(closes, 14)
    assert val > 99.0


def test_rsi_constant_falling():
    closes = list(range(30, 1, -1))
    val = indicators.rsi(closes, 14)
    assert val < 1.0


def test_rsi_insufficient():
    with pytest.raises(ValueError):
        indicators.rsi([1, 2, 3], 14)


def test_sma_basic():
    val = indicators.sma([1, 2, 3, 4, 5, 6, 7, 8, 9, 10], 5)
    # Last 5 = [6,7,8,9,10] → mean 8.0
    assert val == pytest.approx(8.0)


def test_sma_insufficient():
    with pytest.raises(ValueError):
        indicators.sma([1, 2, 3], 5)


def test_median_range():
    df = pd.DataFrame({
        "high": [10, 12, 11, 13, 14, 10, 11, 12, 13, 14],
        "low":  [5,  6,  7,  6,  7,  5,  6,  7,  6,  7],
    })
    val = indicators.median_range(df, 10)
    # ranges = [5,6,4,7,7,5,5,5,7,7] → sorted = [4,5,5,5,5,6,7,7,7,7] median=5.5
    assert val == pytest.approx(5.5)

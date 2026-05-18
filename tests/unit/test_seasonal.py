import pytest

from src.strategy.seasonal import SEASONAL_MULTIPLIER, seasonal_multiplier


def test_long_january():
    assert seasonal_multiplier(1, "LONG") == 1.10


def test_long_may():
    assert seasonal_multiplier(5, "LONG") == 0.90


def test_short_always_1():
    for m in range(1, 13):
        assert seasonal_multiplier(m, "SHORT") == 1.0


def test_table_bounds():
    # Max ±10%, neutral in September
    for v in SEASONAL_MULTIPLIER.values():
        assert 0.90 <= v <= 1.10
    assert SEASONAL_MULTIPLIER[9] == 1.00


def test_invalid_month():
    with pytest.raises(ValueError):
        seasonal_multiplier(13, "LONG")

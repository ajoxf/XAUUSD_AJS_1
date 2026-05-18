import pytest

from src.strategy.regime import classify_regime


def test_trending():
    r = classify_regime(atr_20=12.0, atr_50=10.0)
    assert r.regime == "TRENDING"
    assert r.ratio == pytest.approx(1.2)


def test_ranging():
    r = classify_regime(atr_20=8.0, atr_50=10.0)
    assert r.regime == "RANGING"


def test_neutral():
    r = classify_regime(atr_20=10.0, atr_50=10.0)
    assert r.regime == "NEUTRAL"


def test_boundary_trending():
    # ratio = 1.15 exactly → spec says > 1.15 is trending → 1.15 is NEUTRAL
    r = classify_regime(atr_20=11.5, atr_50=10.0)
    assert r.regime == "NEUTRAL"


def test_boundary_ranging():
    # ratio = 0.85 exactly → spec says < 0.85 is ranging → 0.85 is NEUTRAL
    r = classify_regime(atr_20=8.5, atr_50=10.0)
    assert r.regime == "NEUTRAL"


def test_invalid_atr50():
    with pytest.raises(ValueError):
        classify_regime(atr_20=10.0, atr_50=0.0)

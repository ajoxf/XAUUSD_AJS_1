from datetime import date, datetime, timezone

import pytest

from src.strategy import premarket, sizing
from tests.conftest import synthetic_daily, synthetic_h4


def _ctx(d=date(2024, 6, 25)):
    df = synthetic_daily(d, n=220, daily_range=30.0)
    h4 = synthetic_h4(datetime(d.year, d.month, d.day, 13, 30, tzinfo=timezone.utc), n=220)
    return premarket.build_premarket(d, df, h4["close"])


def test_basic_long_sizing(settings):
    ctx = _ctx()
    entry = ctx.long_entry
    s = sizing.compute_size(ctx, settings, "LONG", entry, 100_000.0)
    # SL distance = entry - prev_close = 0.382 × range
    expected_sl = 0.382 * ctx.range
    assert s.sl_distance == pytest.approx(expected_sl, rel=1e-9)
    # Risk amount = 3% of 100k = 3000
    assert s.risk_amount == 3000.0
    # Lots positive, rounded to 0.01
    assert s.lots_final > 0
    assert round(s.lots_final * 100) == s.lots_final * 100
    # Tranches sum (within rounding) to lots_final
    assert pytest.approx(s.tranche_1 + s.tranche_2 + s.tranche_3,
                          abs=settings.lot_step) == s.lots_final


def test_50_50_split_pcts(settings):
    """v3.2: 50/50 split — H1 = tranche_1, H2 = tranche_2, tranche_3 unused."""
    ctx = _ctx()
    s = sizing.compute_size(ctx, settings, "LONG", ctx.long_entry, 100_000.0)
    total = s.tranche_1 + s.tranche_2 + s.tranche_3
    assert s.tranche_3 == 0.0
    assert s.tranche_1 / total == pytest.approx(0.50, abs=0.02)
    assert s.tranche_2 / total == pytest.approx(0.50, abs=0.02)
    # Backward-compat accessors
    assert s.half_1 == s.tranche_1
    assert s.half_2 == s.tranche_2


def test_seasonal_long_applied(settings):
    # January = 1.10 multiplier
    ctx = _ctx(date(2024, 1, 15))
    s_long = sizing.compute_size(ctx, settings, "LONG", ctx.long_entry, 100_000.0)
    s_short = sizing.compute_size(ctx, settings, "SHORT", ctx.short_entry, 100_000.0)
    # Long should have larger position than short (all else equal in test data)
    # because seasonal_mult_long=1.10
    assert s_long.seasonal_mult == 1.10
    assert s_short.seasonal_mult == 1.0


def test_short_seasonal_always_1(settings):
    ctx = _ctx(date(2024, 1, 15))
    s = sizing.compute_size(ctx, settings, "SHORT", ctx.short_entry, 100_000.0)
    assert s.seasonal_mult == 1.0


def test_actual_risk_audit_computed(settings):
    ctx = _ctx()
    s = sizing.compute_size(ctx, settings, "LONG", ctx.long_entry, 100_000.0)
    expected = s.lots_final * settings.contract_size * s.sl_distance
    assert s.actual_risk == pytest.approx(expected, rel=1e-9)
    assert s.deviation_pct >= 0.0


def test_risk_override(settings):
    ctx = _ctx()
    s_full = sizing.compute_size(ctx, settings, "LONG", ctx.long_entry, 100_000.0)
    s_reduced = sizing.compute_size(ctx, settings, "LONG", ctx.long_entry,
                                     100_000.0, risk_pct_override=0.015)
    # Half-risk override → roughly half lots (modulo rounding)
    assert s_reduced.lots_final <= s_full.lots_final
    assert s_reduced.risk_amount == 1500.0


def test_minimum_lot_floor(settings):
    ctx = _ctx()
    # Tiny equity → would round to zero, but should clamp to 0.01
    s = sizing.compute_size(ctx, settings, "LONG", ctx.long_entry, 100.0)
    assert s.lots_final >= settings.lot_step

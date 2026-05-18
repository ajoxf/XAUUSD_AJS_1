from datetime import date, datetime, timezone

from src.strategy.safety import (CircuitBreaker, ConsecutiveLossCounter,
                                   DailyTradeLock, WinRateMonitor)


def test_daily_lock_persists_within_day():
    lock = DailyTradeLock()
    today = date(2024, 6, 14)
    assert not lock.has_fired(today)
    lock.mark_fired(today)
    assert lock.has_fired(today)
    # Same day, even after reset(): not reset until next day
    assert lock.has_fired(today)


def test_daily_lock_resets_next_day():
    lock = DailyTradeLock()
    lock.mark_fired(date(2024, 6, 14))
    lock.reset()
    assert not lock.has_fired(date(2024, 6, 15))


def test_win_rate_monitor_fast_brake():
    mon = WinRateMonitor(base_risk=0.03)
    # Fewer than 20 trades → base risk
    for _ in range(19):
        mon.record(False)
    assert mon.evaluate() == 0.03
    # 20th false → fast active
    mon.record(False)
    assert mon.evaluate() == 0.02
    assert mon.fast_active


def test_win_rate_monitor_slow_brake_requires_two_breaches():
    mon = WinRateMonitor(base_risk=0.03)
    # Fill 50 with 40% wins → slow rate = 0.40 < 0.42, breach 1
    for i in range(50):
        mon.record(i < 20)
    mon.evaluate()
    assert not mon.slow_active   # one breach not enough
    # Another low entry → re-evaluate, breach 2
    mon.record(False)
    mon.evaluate()
    assert mon.slow_active


def test_consecutive_loss_pause():
    counter = ConsecutiveLossCounter()
    today = date(2024, 6, 14)
    for i in range(6):
        triggered = counter.record(True, today, threshold=7)
        assert not triggered
    triggered = counter.record(True, today, threshold=7)
    assert triggered
    assert counter.is_paused(today)


def test_consecutive_loss_resets_on_win():
    counter = ConsecutiveLossCounter()
    today = date(2024, 6, 14)
    for _ in range(3):
        counter.record(True, today, threshold=7)
    counter.record(False, today, threshold=7)
    assert counter.count == 0


def test_circuit_breaker_trips():
    cb = CircuitBreaker(starting_equity=100_000.0)

    class _S:
        circuit_breaker_pct = 0.30
    s = _S()
    now = datetime.now(tz=timezone.utc)
    assert not cb.check(80_000.0, s, now)   # only 20% down
    assert cb.check(69_000.0, s, now)        # 31% down
    assert cb.triggered
    assert cb.triggered_at_equity == 69_000.0

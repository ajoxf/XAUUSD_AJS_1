"""Risk safety controls - spec §8."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Deque, Optional

from config.flags import FLAGS
from config.settings import Settings


@dataclass
class DailyTradeLock:
    """Hard date-keyed lock - Fix L2."""
    fired_date: Optional[date] = None

    def has_fired(self, today: date) -> bool:
        return self.fired_date == today

    def mark_fired(self, today: date) -> None:
        self.fired_date = today

    def reset(self) -> None:
        self.fired_date = None


@dataclass
class WinRateMonitor:
    """Dual-speed monitor - Fix L9, enhanced v3.0.

    Stores TP1 outcomes (True = TP1 reached, False = SL before TP1).
    """
    base_risk: float
    history: Deque[bool] = field(default_factory=lambda: deque(maxlen=200))
    fast_active: bool = False
    slow_active: bool = False
    slow_consec_breaches: int = 0
    last_slow_window_passed: bool = True

    def record(self, tp1_reached: bool) -> None:
        self.history.append(tp1_reached)

    def _rate(self, n: int) -> Optional[float]:
        if len(self.history) < n:
            return None
        slice_ = list(self.history)[-n:]
        return sum(slice_) / n

    def evaluate(self) -> float:
        """Returns active risk percentage. Caller should use this for sizing."""
        if not FLAGS.FIX_L9_FAST_WINRATE_MONITOR:
            return self.base_risk

        fast = self._rate(20)
        slow = self._rate(50)

        if fast is not None:
            if not self.fast_active and fast < 0.38:
                self.fast_active = True
            elif self.fast_active and fast >= 0.48:
                self.fast_active = False

        if slow is not None:
            if slow < 0.42:
                self.slow_consec_breaches += 1
                if self.slow_consec_breaches >= 2:
                    self.slow_active = True
            else:
                self.slow_consec_breaches = 0
                if self.slow_active and slow >= 0.50:
                    self.slow_active = False

        if self.slow_active:
            return 0.015
        if self.fast_active:
            return 0.020
        return self.base_risk


@dataclass
class ConsecutiveLossCounter:
    """Spec §8C."""
    count: int = 0
    paused_for_date: Optional[date] = None

    def record(self, was_loss: bool, today: date, threshold: int) -> bool:
        if was_loss:
            self.count += 1
        else:
            self.count = 0
        if self.count >= threshold:
            self.paused_for_date = today
            return True
        return False

    def is_paused(self, today: date) -> bool:
        return self.paused_for_date == today

    def reset_for_new_day(self, today: date) -> None:
        if self.paused_for_date is not None and self.paused_for_date != today:
            self.count = 0
            self.paused_for_date = None


@dataclass
class CircuitBreaker:
    """Spec §8A. Halts trading if equity falls below threshold."""
    starting_equity: float
    triggered: bool = False
    triggered_at_utc: Optional[datetime] = None
    triggered_at_equity: Optional[float] = None

    def check(self, current_equity: float, settings: Settings,
              now_utc: datetime) -> bool:
        threshold = self.starting_equity * (1.0 - settings.circuit_breaker_pct)
        if not self.triggered and current_equity < threshold:
            self.triggered = True
            self.triggered_at_utc = now_utc
            self.triggered_at_equity = current_equity
        return self.triggered

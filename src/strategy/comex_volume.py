"""COMEX GC1! 15-min volume tracker — Opt 1 v3.2.

Receives 15-min volume updates (via webhook or direct feed) and tracks a
rolling 20-bar baseline. Detects "consecutive weak bars" (≥2 bars under 50%
of average) which signals momentum fade and triggers half-2 exit.

The webhook receiver lives in `src.integrations.webhook_server` and is
optional — if no feed is configured, callers should detect staleness via
`is_stale()` and fall back to standard trail behaviour.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Deque, Optional


@dataclass(frozen=True)
class VolumeReading:
    volume: float
    time_utc: datetime


class ComexVolumeTracker:
    """Thread-safe rolling window of COMEX 15-min volume bars."""

    def __init__(self,
                 max_history: int = 30,
                 stale_after_minutes: int = 20,
                 weak_threshold_pct: float = 0.50,
                 consecutive_to_exit: int = 2):
        self._history: Deque[VolumeReading] = deque(maxlen=max_history)
        self._lock = Lock()
        self._consecutive_weak: int = 0
        self.stale_after = timedelta(minutes=stale_after_minutes)
        self.weak_threshold_pct = weak_threshold_pct
        self.consecutive_to_exit = consecutive_to_exit

    def update(self, volume: float, time_utc: Optional[datetime] = None) -> bool:
        """Record a new 15-min volume bar. Returns True if this update triggers
        a consecutive-weak streak that should exit the position."""
        ts = time_utc or datetime.now(tz=timezone.utc)
        with self._lock:
            prior = list(self._history)
            self._history.append(VolumeReading(volume=volume, time_utc=ts))

            if len(prior) < 5:
                return False
            avg = sum(v.volume for v in prior[-20:]) / min(len(prior), 20)
            if avg <= 0:
                return False

            if volume < avg * self.weak_threshold_pct:
                self._consecutive_weak += 1
            else:
                self._consecutive_weak = 0

            return self._consecutive_weak >= self.consecutive_to_exit

    def is_stale(self, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(tz=timezone.utc)
        with self._lock:
            if not self._history:
                return True
            latest = self._history[-1].time_utc
            return (now - latest) > self.stale_after

    @property
    def consecutive_weak(self) -> int:
        with self._lock:
            return self._consecutive_weak

    def latest_volume(self) -> Optional[float]:
        with self._lock:
            if not self._history:
                return None
            return self._history[-1].volume

    def avg_20(self) -> Optional[float]:
        with self._lock:
            if len(self._history) < 5:
                return None
            recent = list(self._history)[-20:]
            return sum(v.volume for v in recent) / len(recent)

    def reset_streak(self) -> None:
        with self._lock:
            self._consecutive_weak = 0

    def clear(self) -> None:
        with self._lock:
            self._history.clear()
            self._consecutive_weak = 0

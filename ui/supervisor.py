"""Engine lifecycle supervisor for the Streamlit UI.

Owns a single Engine + broker + background thread. Provides thread-safe
snapshots of state so the Streamlit UI can render without races.

Paper mode replays the most recent 30 trading days through the same Engine
code path that runs live — non-technical users get a live-looking demo with
no broker required.
"""
from __future__ import annotations

import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from config.settings import Settings
from src.broker.adapter import BrokerAdapter
from src.broker.paper_adapter import PaperAdapter
from src.data.feed import DataFeed
from src.data.yfinance_feed import YFinanceFeed
from src.engine.logger import StructuredLogger
from src.engine.runner import Engine
from src.strategy.entry import Candle, Direction


@dataclass
class SupervisorSnapshot:
    """Immutable view of engine state safe to read from any thread."""
    status: str
    mode: str
    last_heartbeat: Optional[datetime]
    last_error: Optional[str]
    equity: float
    starting_equity: float
    peak_equity: float
    drawdown_pct: float

    today: Optional[date]
    premarket: Optional[Dict[str, Any]]
    position: Optional[Dict[str, Any]]
    week: Dict[str, Any]
    monitor: Dict[str, Any]
    recent_events: List[Dict[str, Any]] = field(default_factory=list)


class EngineSupervisor:
    """Lifecycle owner for the bot. Single instance per Streamlit session."""

    def __init__(self, settings: Settings):
        self.settings = settings
        self.engine: Optional[Engine] = None
        self.broker: Optional[BrokerAdapter] = None
        self.logger: Optional[StructuredLogger] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.status: str = "stopped"
        self.last_error: Optional[str] = None
        self.last_heartbeat: Optional[datetime] = None
        # Paper replay state
        self._replay_idx: int = 0
        self._replay_data: Optional[pd.DataFrame] = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── Control ───────────────────────────────────────────
    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self.last_error = None
        self.status = "starting"
        self._thread = threading.Thread(target=self._safe_run, daemon=True,
                                         name="xauusd-engine")
        self._thread.start()

    def stop(self) -> None:
        if not self.is_running:
            self.status = "stopped"
            return
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=10)
        try:
            if self.broker:
                self.broker.disconnect()
        except Exception:
            pass
        self.status = "stopped"

    # ── Thread body ───────────────────────────────────────
    def _safe_run(self) -> None:
        try:
            self._run()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            self.status = "error"

    def _run(self) -> None:
        self.logger = StructuredLogger(self.settings.log_dir, level=self.settings.log_level)
        if self.settings.mode == "live":
            from src.broker.mt5_adapter import MT5Adapter
            self.broker = MT5Adapter(self.settings)
        else:
            self.broker = PaperAdapter(starting_equity=self.settings.starting_equity)
        self.broker.connect()

        feed = YFinanceFeed()
        with self._lock:
            self.engine = Engine(self.settings, self.broker, feed, self.logger)

        if self.settings.mode == "live":
            self._run_live_loop()
        else:
            self._run_paper_replay()

    def _run_live_loop(self) -> None:
        self.status = "running"
        current_day: Optional[date] = None
        while not self._stop.is_set():
            now = datetime.now(tz=timezone.utc)
            if now.date() != current_day:
                current_day = now.date()
                with self._lock:
                    self.engine.start_day(current_day)
            try:
                if self.engine.state.position and not self.engine.state.position.closed:
                    quote = self.broker.quote(self.settings.symbol)
                    with self._lock:
                        self.engine.on_tick(quote.mid, now)
            except Exception as exc:
                self.logger.warn(f"Live tick failure: {exc}")
            self.last_heartbeat = datetime.now(tz=timezone.utc)
            if self._stop.wait(3.0):
                break

    def _run_paper_replay(self) -> None:
        """Replay last ~30 trading days through the engine, one tick per second
        across the synthesised intraday path. Educational demo of the live loop."""
        self.status = "running"
        try:
            feed = YFinanceFeed()
            end = datetime.now(tz=timezone.utc).date()
            replay_days = feed.daily(self.settings.symbol, end, lookback_days=30)
        except Exception as exc:
            self.last_error = f"yfinance unavailable for replay: {exc}"
            self.status = "error"
            return

        self._replay_data = replay_days
        for trade_date, day_row in replay_days.iterrows():
            if self._stop.is_set():
                break
            self._replay_idx += 1
            trade_date_ = trade_date.date() if hasattr(trade_date, "date") else trade_date
            with self._lock:
                ctx = self.engine.start_day(trade_date_)
            if ctx is None:
                if self._stop.wait(0.5):
                    break
                continue

            # Synthesise an intraday path: open → high → low → close (or O→L→H→C
            # if close < open). 60 steps across the session.
            path = self._intraday_path(day_row)
            session_start = ctx.session_start_utc
            session_end = ctx.session_end_utc
            n_steps = len(path)
            step_dt = (session_end - session_start) / n_steps

            # First, evaluate signal at the candle just after open
            self._maybe_fire_signal(ctx, day_row, path)

            for i, price in enumerate(path):
                if self._stop.is_set():
                    break
                tick_time = session_start + step_dt * i
                self.broker.set_quote(mid=price, time_utc=tick_time)
                with self._lock:
                    self.engine.on_tick(price, tick_time)
                self.last_heartbeat = tick_time
                if self._stop.wait(0.25):
                    break

            # Force-close at session end
            with self._lock:
                if self.engine.state.position and not self.engine.state.position.closed:
                    from src.strategy.exits import force_close_session_end
                    closed = force_close_session_end(self.engine.state.position,
                                                       float(day_row["close"]), session_end)
                    for t in closed:
                        self.engine._record_close(t)
                    self.engine._post_trade_bookkeeping(self.engine.state.position)

        self.status = "completed"

    def _intraday_path(self, day_row: pd.Series, n: int = 40) -> List[float]:
        """Generate a price path open→high→low→close (or O→L→H→C)."""
        o, h, l, c = (float(day_row["open"]), float(day_row["high"]),
                       float(day_row["low"]), float(day_row["close"]))
        bullish = c >= o
        # Path waypoints
        if bullish:
            waypoints = [o, h, l, c]
        else:
            waypoints = [o, l, h, c]
        # Linearly interpolate between waypoints
        steps_per_seg = n // 3
        path: List[float] = []
        for i in range(3):
            start, end = waypoints[i], waypoints[i + 1]
            for j in range(steps_per_seg):
                path.append(start + (end - start) * j / steps_per_seg)
        path.append(c)
        return path

    def _maybe_fire_signal(self, ctx, day_row: pd.Series, path: List[float]) -> None:
        """Synthesise a confirmation candle from the first part of the path
        and feed it through the standard evaluate_signal pathway."""
        if ctx.trend_bias not in ("LONG_ONLY", "BOTH"):
            direction = Direction.SHORT
            level = ctx.short_entry
        else:
            direction = Direction.LONG
            level = ctx.long_entry

        # Did the path actually cross the level?
        crossed = (max(path) >= level) if direction == Direction.LONG else (min(path) <= level)
        if not crossed:
            return

        # Build a confirmation candle straddling the level
        if direction == Direction.LONG:
            conf = Candle(open=level - 0.5, high=max(path[:10]) if len(path) >= 10 else level + 2,
                          low=level - 1.0, close=level + 1.5)
            nxt = Candle(open=conf.close, high=conf.close + 1.5,
                         low=conf.close - 0.5, close=conf.close + 1.0)
        else:
            conf = Candle(open=level + 0.5, high=level + 1.0,
                          low=min(path[:10]) if len(path) >= 10 else level - 2, close=level - 1.5)
            nxt = Candle(open=conf.close, high=conf.close + 0.5,
                         low=conf.close - 1.5, close=conf.close - 1.0)

        # RSI feed: use a synthetic neutral-zone series
        closes_15m = self._neutral_rsi_closes(direction)

        # Move broker quote to entry level so open_market fills here
        self.broker.set_quote(mid=level, time_utc=ctx.session_start_utc + timedelta(minutes=15))
        with self._lock:
            self.engine.evaluate_signal(
                now_utc=ctx.session_start_utc + timedelta(minutes=15),
                direction=direction,
                confirmation_candle=conf,
                next_candle=nxt,
                closes_15m=closes_15m,
            )

    @staticmethod
    def _neutral_rsi_closes(direction: Direction) -> List[float]:
        """Closes that produce RSI ≈ 55–60 (long zone) or 40–45 (short zone)."""
        closes = [100.0]
        if direction == Direction.LONG:
            for i in range(20):
                closes.append(closes[-1] + (0.4 if i % 2 == 0 else -0.35))
        else:
            for i in range(20):
                closes.append(closes[-1] + (-0.4 if i % 2 == 0 else 0.35))
        return closes

    # ── Snapshot ──────────────────────────────────────────
    def snapshot(self, recent_events_limit: int = 50) -> SupervisorSnapshot:
        with self._lock:
            engine = self.engine
            equity = self.broker.equity() if self.broker else self.settings.starting_equity
            premarket = None
            position = None
            week = {}
            monitor = {"active_risk_pct": self.settings.risk_pct * 100,
                       "fast_active": False, "slow_active": False,
                       "consecutive_losses": 0, "win_rate_fast_20": None,
                       "win_rate_slow_50": None}
            today = None
            peak = self.settings.starting_equity
            start_eq = self.settings.starting_equity

            if engine is not None:
                state = engine.state
                today = state.today
                start_eq = state.starting_equity
                peak = state.peak_equity
                if state.premarket is not None:
                    premarket = _premarket_view(state.premarket)
                if state.position is not None and not state.position.closed:
                    position = _position_view(state.position)
                week = {
                    "trades": state.week_trades,
                    "wins_tp1": state.week_wins_tp1,
                    "wins_tp2": state.week_wins_tp2,
                    "sls": state.week_sls,
                    "regime_exits": state.week_regime_exits,
                    "tp1_accelerations": state.week_tp1_accels,
                    "wednesday_accelerations": state.week_wednesday_accels,
                    "comex_vol_exits": state.week_comex_vol_exits,
                    "rsi_trims": state.week_rsi_trims,
                    "back_to_back_tp2": state.week_back_to_back_tp2,
                    "high_atr_tp2": state.week_high_atr_tp2,
                    "session_close_half2": state.week_session_close_half2,
                    "session_close_full": state.week_session_close_full,
                    "b_fails": state.week_b_fails,
                    "c_fails": state.week_c_fails,
                    "d_fails": state.week_d_fails,
                    "gate_block_event": state.gate_block_event,
                    "gate_block_trend": state.gate_block_trend,
                    "gate_block_sma200": state.gate_block_sma200,
                    "gate_block_daily_lock": state.gate_block_daily_lock,
                    "gate_block_atr_floor": state.gate_block_atr_floor,
                    "gate_block_atr_cap": state.gate_block_atr_cap,
                }
                mon = state.win_rate_monitor
                monitor = {
                    "active_risk_pct": round(mon.evaluate() * 100, 3),
                    "fast_active": mon.fast_active,
                    "slow_active": mon.slow_active,
                    "consecutive_losses": state.loss_counter.count,
                    "win_rate_fast_20": (round(mon._rate(20) * 100, 1)
                                          if mon._rate(20) is not None else None),
                    "win_rate_slow_50": (round(mon._rate(50) * 100, 1)
                                          if mon._rate(50) is not None else None),
                }

            drawdown_pct = max(0.0, (peak - equity) / peak * 100.0) if peak > 0 else 0.0
            recent_events = self._read_recent_events(recent_events_limit)

            return SupervisorSnapshot(
                status=self.status,
                mode=self.settings.mode,
                last_heartbeat=self.last_heartbeat,
                last_error=self.last_error,
                equity=equity,
                starting_equity=start_eq,
                peak_equity=peak,
                drawdown_pct=drawdown_pct,
                today=today,
                premarket=premarket,
                position=position,
                week=week,
                monitor=monitor,
                recent_events=recent_events,
            )

    def _read_recent_events(self, n: int) -> List[Dict[str, Any]]:
        import json
        log_path = self.settings.log_dir / "events.jsonl"
        if not log_path.exists():
            return []
        try:
            with log_path.open() as f:
                lines = f.readlines()
            tail = lines[-n:]
            return [json.loads(ln) for ln in tail if ln.strip()]
        except Exception:
            return []


def _premarket_view(ctx) -> Dict[str, Any]:
    return {
        "date": ctx.trade_date,
        "range": round(ctx.range, 2),
        "atr_20": round(ctx.atr_20, 2),
        "atr_50": round(ctx.atr_50, 2),
        "sma200_daily": round(ctx.sma200_daily, 2),
        "regime": ctx.regime,
        "regime_ratio": round(ctx.regime_ratio, 3),
        "trend_bias": ctx.trend_bias,
        "prev_close": round(ctx.prev_close, 2),
        "long_entry": round(ctx.long_entry, 2),
        "long_sl": round(ctx.long_sl, 2),
        "long_tp1": round(ctx.long_tp1, 2),
        "long_tp2": round(ctx.long_tp2, 2),
        "long_tp2_fib": round(ctx.long_tp2_fib, 3),
        "short_entry": round(ctx.short_entry, 2),
        "short_sl": round(ctx.short_sl, 2),
        "short_tp1": round(ctx.short_tp1, 2),
        "short_tp2": round(ctx.short_tp2, 2),
        "short_tp2_fib": round(ctx.short_tp2_fib, 3),
        "filter_c_active": ctx.filter_c_active,
        "long_filter_f": ctx.long_filter_f,
        "short_filter_f": ctx.short_filter_f,
        "back_to_back_active": ctx.back_to_back_active,
        "high_atr_extension_active": ctx.high_atr_extension_active,
        "session_start_utc": ctx.session_start_utc,
        "session_end_utc": ctx.session_end_utc,
        "event_blocked": ctx.event_blocked,
        "event_reason": ctx.event_reason,
        "seasonal_mult_long": ctx.seasonal_mult_long,
        "prev_session_close_type": ctx.prev_session_close_type,
    }


def _position_view(pos) -> Dict[str, Any]:
    return {
        "direction": pos.direction,
        "entry_price": round(pos.entry_price, 2),
        "entry_time_utc": pos.entry_time_utc,
        "current_stop": round(pos.current_stop, 2),
        "tp1": round(pos.tp1, 2),
        "tp2": round(pos.tp2, 2),
        "tp1_hit": pos.tp1_hit,
        "tp2_hit": pos.tp2_hit,
        "accelerated_tp1": pos.accelerated_tp1,
        "accelerated_kind": pos.accelerated_kind,
        "rsi_trim_done": pos.rsi_trim_done,
        "tranches": [
            {"name": t.name, "lots": t.lots, "open": t.is_open,
             "close_price": t.close_price,
             "close_reason": t.close_reason.value if t.close_reason else None,
             "partial_closes": t.partial_closes}
            for t in pos.tranches
        ],
    }

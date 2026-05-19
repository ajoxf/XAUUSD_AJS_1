"""Engine lifecycle supervisor.

Owns a single Engine + broker + background thread. Provides thread-safe
snapshots of state. Paper mode replays the last 30 trading days through
the live engine code path for demos. Live mode polls MT5 quotes every 3s.
"""
from __future__ import annotations

import logging
import threading
import time
import traceback
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import pandas as pd

from config.settings import Settings
from src.broker.adapter import BrokerAdapter
from src.broker.paper_adapter import PaperAdapter
from src.data.yfinance_feed import YFinanceFeed
from src.engine.logger import StructuredLogger
from src.engine.runner import Engine
from src.strategy.comex_volume import ComexVolumeTracker
from src.strategy.entry import Candle, Direction

log = logging.getLogger("xauusd-bot.supervisor")


@dataclass
class SupervisorSnapshot:
    status: str
    mode: str
    last_heartbeat: Optional[datetime]
    last_error: Optional[str]
    equity: float
    balance: float
    balance_source: str
    starting_equity: float
    peak_equity: float
    drawdown_pct: float
    today: Optional[date]
    premarket: Optional[Dict[str, Any]]
    position: Optional[Dict[str, Any]]
    pending_entry: Optional[Dict[str, Any]]
    week: Dict[str, Any]
    monitor: Dict[str, Any]
    recent_events: List[Dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "status": self.status,
            "mode": self.mode,
            "last_heartbeat": self.last_heartbeat.isoformat() if self.last_heartbeat else None,
            "last_error": self.last_error,
            "equity": self.equity,
            "balance": self.balance,
            "balance_source": self.balance_source,
            "starting_equity": self.starting_equity,
            "peak_equity": self.peak_equity,
            "drawdown_pct": self.drawdown_pct,
            "today": self.today.isoformat() if self.today else None,
            "premarket": self.premarket,
            "position": self.position,
            "pending_entry": self.pending_entry,
            "week": self.week,
            "monitor": self.monitor,
            "recent_events": self.recent_events,
        }


class EngineSupervisor:
    """Process-wide singleton (per-app). Thread-safe."""

    def __init__(self, settings: Settings,
                 comex_tracker: Optional[ComexVolumeTracker] = None):
        self.settings = settings
        self.comex_tracker = comex_tracker
        self.engine: Optional[Engine] = None
        self.broker: Optional[BrokerAdapter] = None
        # Optional read-only MT5 connection used in paper mode to surface
        # the real account balance on the dashboard while paper trades run.
        self.reference_broker: Optional[BrokerAdapter] = None
        self.logger: Optional[StructuredLogger] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()
        self.status: str = "stopped"
        self.last_error: Optional[str] = None
        self.last_heartbeat: Optional[datetime] = None

        # Eager broker connect for account-info display before the engine
        # starts. Live mode → attach to MT5 if running. Paper → instantiate
        # the simulated broker with starting_equity from .env.
        self._try_eager_connect()

    def _try_eager_connect(self) -> None:
        """Attach to MT5 at app startup so the dashboard shows the real
        account balance from the first page load.

        - Live mode: attaches as the TRADING broker. When Start is pressed,
          the engine reuses this same connection.
        - Paper mode: attaches as a REFERENCE-ONLY broker. The PaperAdapter
          is still used for trade simulation. The dashboard prefers the
          reference broker's balance/equity so the user can see their real
          account size while the bot trades in sim.

        Failures are non-fatal — the user can retry by pressing Start
        (which re-runs the connect path for the trading broker)."""
        try:
            from src.broker.mt5_adapter import MT5Adapter
            adapter = MT5Adapter(self.settings)
            adapter.connect()
            bal = adapter.balance()
            eq = adapter.equity()
            if self.settings.mode == "live":
                self.broker = adapter
                log.info("Pre-connect to MT5 ✓ balance=$%.2f equity=$%.2f "
                          "(trading broker — press Start to begin)", bal, eq)
            else:
                self.reference_broker = adapter
                log.info("MT5 reference attached ✓ balance=$%.2f equity=$%.2f "
                          "(paper-trading; balance shown is your real MT5 account)",
                          bal, eq)
        except Exception as exc:
            if self.settings.mode == "live":
                log.warning("Eager MT5 pre-connect skipped: %s "
                             "(start MT5 + log in, then press Start)", exc)
                self.broker = None
            else:
                log.info("MT5 reference unavailable: %s "
                          "(paper-balance fallback)", exc)
                self.reference_broker = None

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # ── Lifecycle ────────────────────────────────────────
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
        # Keep the reference broker alive for ongoing balance display
        self.status = "stopped"

    def update_settings(self, settings: Settings) -> None:
        """Apply new settings — caller should ensure the engine is stopped."""
        with self._lock:
            self.settings = settings

    # ── Manual confirm / cancel ──────────────────────────
    def confirm_pending(self) -> bool:
        with self._lock:
            if self.engine is None:
                return False
            return self.engine.confirm_pending(datetime.now(tz=timezone.utc))

    def cancel_pending(self) -> bool:
        with self._lock:
            if self.engine is None:
                return False
            return self.engine.cancel_pending(datetime.now(tz=timezone.utc))

    # ── Thread body ──────────────────────────────────────
    def _safe_run(self) -> None:
        try:
            self._run()
        except Exception as exc:
            self.last_error = f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}"
            self.status = "error"
            log.exception("Engine thread crashed")

    def _run(self) -> None:
        self.logger = StructuredLogger(self.settings.log_dir, level=self.settings.log_level)
        log.info("Engine starting in %s mode (symbol=%s, risk=%.2f%%)",
                  self.settings.mode.upper(), self.settings.symbol,
                  self.settings.risk_pct * 100)
        # If pre-connect succeeded in __init__, reuse that broker.
        if self.broker is None:
            if self.settings.mode == "live":
                from src.broker.mt5_adapter import MT5Adapter
                login_mode = "attach" if not self.settings.mt5_login else "credential"
                log.info("Connecting to MT5 in %s mode…", login_mode)
                self.broker = MT5Adapter(self.settings)
            elif self.settings.mode == "dryrun":
                from src.broker.dryrun_adapter import DryRunAdapter
                from src.broker.mt5_adapter import MT5Adapter
                log.info("Dry-run mode: wrapping MT5 reads but blocking all writes")
                self.broker = DryRunAdapter(MT5Adapter(self.settings),
                                              symbol=self.settings.symbol)
            else:
                log.info("Paper broker initialised — replaying recent history")
                self.broker = PaperAdapter(starting_equity=self.settings.starting_equity)
            try:
                self.broker.connect()
            except Exception as exc:
                log.error("Broker connection failed: %s", exc)
                raise
        else:
            log.info("Re-using pre-connected broker (%s)",
                      type(self.broker).__name__)

        with self._lock:
            self.engine = Engine(self.settings, self.broker,
                                  YFinanceFeed(), self.logger,
                                  comex_tracker=self.comex_tracker)
        log.info("Engine ready · equity=$%.2f · CB threshold=%.0f%%",
                  self.broker.equity(),
                  self.settings.circuit_breaker_pct * 100)

        if self.settings.mode == "live":
            self._run_live_loop()
        else:
            self._run_paper_replay()

    def _run_live_loop(self) -> None:
        self.status = "running"
        log.info("Live loop started · polling MT5 every 3s · heartbeat every 60s")
        current_day: Optional[date] = None
        last_heartbeat_console = 0.0
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

            # 60s console heartbeat — current price + open position summary
            wall = time.time()
            if wall - last_heartbeat_console > 60.0:
                last_heartbeat_console = wall
                self._log_console_heartbeat()

            self.last_heartbeat = datetime.now(tz=timezone.utc)
            if self._stop.wait(3.0):
                break
        log.info("Live loop stopped")

    def _log_console_heartbeat(self) -> None:
        try:
            q = self.broker.quote(self.settings.symbol)
            equity = self.broker.equity()
            pos = self.engine.state.position if self.engine else None
            if pos and not pos.closed:
                tags = []
                if pos.tp1_hit: tags.append("TP1✓")
                if pos.tp2_hit: tags.append("TP2✓")
                if pos.accelerated_tp1: tags.append(f"accel-{pos.accelerated_kind}")
                tag = " ".join(tags) or "open"
                log.info(
                    "[heartbeat] bid=%.2f ask=%.2f spread=%.2f · equity=$%.2f · "
                    "POS %s @ %.2f stop=%.2f tp1=%.2f tp2=%.2f %s",
                    q.bid, q.ask, q.spread, equity,
                    pos.direction, pos.entry_price, pos.current_stop,
                    pos.tp1, pos.tp2, tag,
                )
            else:
                log.info(
                    "[heartbeat] bid=%.2f ask=%.2f spread=%.2f · equity=$%.2f · flat",
                    q.bid, q.ask, q.spread, equity,
                )
        except Exception as exc:
            log.debug("Heartbeat skipped: %s", exc)

    def _run_paper_replay(self) -> None:
        self.status = "running"
        log.info("Paper replay starting · fetching last 30 trading days from yfinance…")
        try:
            feed = YFinanceFeed()
            end = datetime.now(tz=timezone.utc).date()
            replay = feed.daily(self.settings.symbol, end, lookback_days=30)
            log.info("Replay loaded %d trading days (%s → %s)",
                      len(replay), replay.index[0], replay.index[-1])
        except Exception as exc:
            self.last_error = f"yfinance unavailable for replay: {exc}"
            log.error("Paper replay failed: %s", exc)
            self.status = "error"
            return

        for trade_date, day_row in replay.iterrows():
            if self._stop.is_set():
                break
            td = trade_date.date() if hasattr(trade_date, "date") else trade_date
            with self._lock:
                ctx = self.engine.start_day(td)
            if ctx is None:
                if self._stop.wait(0.5):
                    break
                continue

            path = self._intraday_path(day_row)
            n = len(path)
            step_dt = (ctx.session_end_utc - ctx.session_start_utc) / n

            self._maybe_fire_signal(ctx, path)
            for i, price in enumerate(path):
                if self._stop.is_set():
                    break
                tick_time = ctx.session_start_utc + step_dt * i
                self.broker.set_quote(mid=price, time_utc=tick_time)
                with self._lock:
                    self.engine.on_tick(price, tick_time)
                self.last_heartbeat = tick_time
                if self._stop.wait(0.25):
                    break

            with self._lock:
                if self.engine.state.position and not self.engine.state.position.closed:
                    from src.strategy.exits import force_close_session_end
                    closed = force_close_session_end(self.engine.state.position,
                                                      float(day_row["close"]),
                                                      ctx.session_end_utc)
                    for t in closed:
                        self.engine._record_close(t)
                    self.engine._post_trade_bookkeeping(self.engine.state.position)

        self.status = "completed"

    @staticmethod
    def _intraday_path(day_row: pd.Series, n: int = 40) -> List[float]:
        o, h, l, c = (float(day_row["open"]), float(day_row["high"]),
                       float(day_row["low"]), float(day_row["close"]))
        waypoints = [o, h, l, c] if c >= o else [o, l, h, c]
        steps = n // 3
        path: List[float] = []
        for i in range(3):
            a, b = waypoints[i], waypoints[i + 1]
            for j in range(steps):
                path.append(a + (b - a) * j / steps)
        path.append(c)
        return path

    def _maybe_fire_signal(self, ctx, path: List[float]) -> None:
        if ctx.trend_bias not in ("LONG_ONLY", "BOTH"):
            direction = Direction.SHORT
            level = ctx.short_entry
        else:
            direction = Direction.LONG
            level = ctx.long_entry
        crossed = (max(path) >= level) if direction == Direction.LONG else (min(path) <= level)
        if not crossed:
            return
        if direction == Direction.LONG:
            conf = Candle(level - 0.5, max(path[:10]) if len(path) >= 10 else level + 2,
                          level - 1.0, level + 1.5)
            nxt = Candle(conf.close, conf.close + 1.5, conf.close - 0.5, conf.close + 1.0)
        else:
            conf = Candle(level + 0.5, level + 1.0,
                          min(path[:10]) if len(path) >= 10 else level - 2, level - 1.5)
            nxt = Candle(conf.close, conf.close + 0.5, conf.close - 1.5, conf.close - 1.0)
        closes_15m = self._neutral_rsi_closes(direction)
        self.broker.set_quote(mid=level,
                               time_utc=ctx.session_start_utc + timedelta(minutes=15))
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
        closes = [100.0]
        if direction == Direction.LONG:
            for i in range(20):
                closes.append(closes[-1] + (0.4 if i % 2 == 0 else -0.35))
        else:
            for i in range(20):
                closes.append(closes[-1] + (-0.4 if i % 2 == 0 else 0.35))
        return closes

    # ── Snapshot ─────────────────────────────────────────
    def snapshot(self, recent_events_limit: int = 50) -> SupervisorSnapshot:
        with self._lock:
            engine = self.engine
            equity, balance, balance_source = self._resolve_balance()
            premarket = position = None
            week: Dict[str, Any] = {}
            today = None
            peak = start_eq = self.settings.starting_equity
            monitor = {"active_risk_pct": self.settings.risk_pct * 100,
                       "fast_active": False, "slow_active": False,
                       "consecutive_losses": 0, "win_rate_fast_20": None,
                       "win_rate_slow_50": None}

            pending = None
            if engine is not None:
                state = engine.state
                today = state.today
                start_eq = state.starting_equity
                peak = state.peak_equity
                if state.premarket is not None:
                    premarket = _premarket_view(state.premarket)
                if state.position is not None and not state.position.closed:
                    position = _position_view(state.position)
                if state.pending_entry is not None:
                    pending = _pending_view(state.pending_entry)
                week = _week_view(state)
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
            return SupervisorSnapshot(
                status=self.status, mode=self.settings.mode,
                last_heartbeat=self.last_heartbeat, last_error=self.last_error,
                equity=equity, balance=balance, balance_source=balance_source,
                starting_equity=start_eq, peak_equity=peak,
                drawdown_pct=drawdown_pct, today=today, premarket=premarket,
                position=position, pending_entry=pending,
                week=week, monitor=monitor,
                recent_events=self._read_recent_events(recent_events_limit),
            )

    def _resolve_balance(self) -> tuple:
        """Returns (equity, balance, source_label) using the best available
        data source: trading broker > reference broker > settings fallback."""
        # 1. Trading broker (live mode after eager connect; or after Start)
        if self.broker is not None and self.settings.mode == "live":
            try:
                return (self.broker.equity(), self.broker.balance(),
                        "live · MT5 account")
            except Exception:
                pass
        # 2. Reference MT5 broker (paper mode with MT5 reachable)
        if self.reference_broker is not None:
            try:
                return (self.reference_broker.equity(),
                        self.reference_broker.balance(),
                        "MT5 reference · paper trading")
            except Exception:
                pass
        # 3. Trading broker for paper mode (engine running)
        if self.broker is not None:
            try:
                return (self.broker.equity(), self.broker.balance(),
                        "paper · simulated")
            except Exception:
                pass
        # 4. Fallback to env starting equity
        return (self.settings.starting_equity, self.settings.starting_equity,
                "paper · simulated")

    def _read_recent_events(self, n: int) -> List[Dict[str, Any]]:
        import json
        path = self.settings.log_dir / "events.jsonl"
        if not path.exists():
            return []
        try:
            with path.open() as f:
                lines = f.readlines()
            return [json.loads(ln) for ln in lines[-n:] if ln.strip()]
        except Exception:
            return []


def _premarket_view(ctx) -> Dict[str, Any]:
    return {
        "date": ctx.trade_date.isoformat(),
        "range": round(ctx.range, 2), "atr_20": round(ctx.atr_20, 2),
        "atr_50": round(ctx.atr_50, 2),
        "atr_short_period": ctx.atr_short_period,
        "atr_long_period": ctx.atr_long_period,
        "sma200_daily": round(ctx.sma200_daily, 2),
        "regime": ctx.regime, "regime_ratio": round(ctx.regime_ratio, 3),
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
        "session_start_utc": ctx.session_start_utc.isoformat(),
        "session_end_utc": ctx.session_end_utc.isoformat(),
        "event_blocked": ctx.event_blocked, "event_reason": ctx.event_reason,
        "seasonal_mult_long": ctx.seasonal_mult_long,
        "prev_session_close_type": ctx.prev_session_close_type,
    }


def _pending_view(pe) -> Dict[str, Any]:
    now = datetime.now(tz=timezone.utc)
    return {
        "direction": pe.direction,
        "entry_price": round(pe.entry_price, 2),
        "entry_kind": pe.entry_kind,
        "sl": round(pe.sl, 2),
        "tp1": round(pe.tp1, 2),
        "tp2": round(pe.tp2, 2),
        "half_1_lots": pe.half_1_lots,
        "half_2_lots": pe.half_2_lots,
        "risk_amount": round(pe.risk_amount, 2),
        "actual_risk": round(pe.actual_risk, 2),
        "deviation_pct": round(pe.deviation_pct, 2),
        "created_at_utc": pe.created_at_utc.isoformat(),
        "timeout_at_utc": pe.timeout_at_utc.isoformat(),
        "remaining_seconds": round(pe.remaining_seconds(now), 1),
    }


def _position_view(pos) -> Dict[str, Any]:
    return {
        "direction": pos.direction,
        "entry_price": round(pos.entry_price, 2),
        "entry_time_utc": pos.entry_time_utc.isoformat() if pos.entry_time_utc else None,
        "current_stop": round(pos.current_stop, 2),
        "tp1": round(pos.tp1, 2), "tp2": round(pos.tp2, 2),
        "tp1_hit": pos.tp1_hit, "tp2_hit": pos.tp2_hit,
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


def _week_view(state) -> Dict[str, Any]:
    return {
        "trades": state.week_trades,
        "wins_tp1": state.week_wins_tp1, "wins_tp2": state.week_wins_tp2,
        "sls": state.week_sls, "regime_exits": state.week_regime_exits,
        "tp1_accelerations": state.week_tp1_accels,
        "wednesday_accelerations": state.week_wednesday_accels,
        "comex_vol_exits": state.week_comex_vol_exits,
        "rsi_trims": state.week_rsi_trims,
        "back_to_back_tp2": state.week_back_to_back_tp2,
        "high_atr_tp2": state.week_high_atr_tp2,
        "session_close_half2": state.week_session_close_half2,
        "session_close_full": state.week_session_close_full,
        "b_fails": state.week_b_fails, "c_fails": state.week_c_fails,
        "d_fails": state.week_d_fails,
        "gate_block_event": state.gate_block_event,
        "gate_block_trend": state.gate_block_trend,
        "gate_block_sma200": state.gate_block_sma200,
        "gate_block_daily_lock": state.gate_block_daily_lock,
        "gate_block_atr_floor": state.gate_block_atr_floor,
        "gate_block_atr_cap": state.gate_block_atr_cap,
    }

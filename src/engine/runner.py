"""Main engine — orchestrates pre-market, gates, entry detection, and exits.

v3.2: 50/50 exit, 20:55 UTC partial close, six return-enhancement opts.
"""
from __future__ import annotations

from dataclasses import asdict
from datetime import date, datetime
from typing import List, Optional, Sequence

from config.flags import FLAGS
from config.settings import Settings
from src.broker.adapter import BrokerAdapter, OrderSide
from src.data.feed import DataFeed
from src.engine.logger import StructuredLogger
from src.engine.state import EngineState
from src.strategy import entry as entry_mod
from src.strategy import exits, gates, premarket, session, sizing
from src.strategy.comex_volume import ComexVolumeTracker
from src.strategy.entry import Candle, Direction
from src.strategy.exits import CloseReason, PositionState, TrancheState
from src.strategy.premarket import PremarketContext
from src.strategy.safety import (CircuitBreaker, ConsecutiveLossCounter,
                                  DailyTradeLock, WinRateMonitor)


class Engine:
    def __init__(
        self,
        settings: Settings,
        broker: BrokerAdapter,
        feed: DataFeed,
        logger: StructuredLogger,
        comex_tracker: Optional[ComexVolumeTracker] = None,
    ):
        self.settings = settings
        self.broker = broker
        self.feed = feed
        self.log = logger
        equity = broker.equity() if settings.mode == "live" else settings.starting_equity
        self.state = EngineState(
            starting_equity=equity,
            peak_equity=equity,
            win_rate_monitor=WinRateMonitor(base_risk=settings.risk_pct),
            circuit_breaker=CircuitBreaker(starting_equity=equity),
            comex_tracker=comex_tracker,
        )

    # ── Daily lifecycle ───────────────────────────────────
    def start_day(self, today: date) -> Optional[PremarketContext]:
        self.state.today = today
        self.state.daily_lock.reset()
        self.state.loss_counter.reset_for_new_day(today)
        self.state.closes_15m_post_tp1 = []
        try:
            daily_df = self.feed.daily(self.settings.symbol, today, lookback_days=210)
            session_start_utc, _ = session.session_window_utc(today)
            h4_df = self.feed.h4(self.settings.symbol, session_start_utc, lookback_bars=60)
            ctx = premarket.build_premarket(
                today, daily_df, h4_df["close"],
                prev_session_close_type=self.state.prev_session_close_type,
                atr_short_period=self.settings.atr_short_period,
                atr_long_period=self.settings.atr_long_period,
            )
        except Exception as exc:
            self.log.warn(f"Pre-market build failed for {today}: {exc}")
            self.state.premarket = None
            return None

        self.state.premarket = ctx
        if ctx.back_to_back_active:
            self.state.week_back_to_back_tp2 += 1
        if ctx.high_atr_extension_active:
            self.state.week_high_atr_tp2 += 1
        self.log.event("premarket", _premarket_payload(ctx))
        return ctx

    # ── Signal evaluation (called per 15m candle close) ──
    def evaluate_signal(
        self,
        now_utc: datetime,
        direction: Direction,
        confirmation_candle: Candle,
        next_candle: Optional[Candle],
        closes_15m: Sequence[float],
    ) -> bool:
        ctx = self.state.premarket
        if ctx is None:
            return False

        if self.state.circuit_breaker.triggered:
            self.log.warn("Trading halted by circuit breaker")
            return False
        if self.state.loss_counter.is_paused(self.state.today):
            return False

        gate_result = gates.check_all_gates(
            ctx, self.settings, now_utc, direction.value,
            self.state.daily_lock.has_fired(self.state.today),
            self.state.position is not None and not self.state.position.closed,
        )
        if not gate_result.passed:
            self._record_gate_block(gate_result.failures)
            self.log.event("gate_block", {"direction": direction.value,
                                          "failures": gate_result.failures})
            return False

        eval_result = entry_mod.evaluate_entry(
            ctx, direction, confirmation_candle, next_candle, closes_15m, self.settings,
        )
        if not eval_result.layer_b.passed:
            self.state.week_b_fails += 1
            self.log.event("entry_reject", {"layer": "B",
                                            "reasons": eval_result.layer_b.reasons})
            return False
        if not eval_result.layer_c.passed:
            self.state.week_c_fails += 1
            self.log.event("entry_reject", {"layer": "C",
                                            "reasons": eval_result.layer_c.reasons})
            return False
        if eval_result.layer_d is None or not eval_result.layer_d.passed:
            self.state.week_d_fails += 1
            reasons = eval_result.layer_d.reasons if eval_result.layer_d else ["pending"]
            self.log.event("entry_reject", {"layer": "D", "reasons": reasons})
            return False

        return self._open_position(now_utc, direction, eval_result.entry_kind or "CONTINUATION",
                                   confirmation_candle, eval_result)

    # ── Position open ─────────────────────────────────────
    def _open_position(
        self, now_utc: datetime, direction: Direction, entry_kind: str,
        confirmation_candle: Candle, eval_result: "entry_mod.EntryEvaluation",
    ) -> bool:
        ctx = self.state.premarket
        equity = self.broker.equity()
        active_risk = self.state.win_rate_monitor.evaluate()
        if self.state.circuit_breaker.check(equity, self.settings, now_utc):
            self.log.warn(f"Circuit breaker tripped at equity {equity:.2f}")
            return False

        quote = self.broker.quote(self.settings.symbol)
        entry_price = quote.ask if direction == Direction.LONG else quote.bid
        if quote.spread > self.settings.max_spread_per_oz:
            self.log.warn(f"Spread {quote.spread:.2f} > max — abort entry")
            return False

        sized = sizing.compute_size(ctx, self.settings, direction.value,
                                    entry_price, equity, risk_pct_override=active_risk)
        if sized.warning:
            self.log.warn(f"Rounding deviation {sized.deviation_pct:.1f}% — "
                          f"actual ${sized.actual_risk:.2f} vs intended ${sized.risk_amount:.2f}")

        side = OrderSide.BUY if direction == Direction.LONG else OrderSide.SELL
        sl = ctx.long_sl if direction == Direction.LONG else ctx.short_sl
        tp1 = ctx.long_tp1 if direction == Direction.LONG else ctx.short_tp1
        tp2 = ctx.long_tp2 if direction == Direction.LONG else ctx.short_tp2

        # v3.2 — 50/50 split: open ONE order, partial-close at TP1
        half_specs = [
            ("H1", sized.half_1, tp1),
            ("H2", sized.half_2, tp2),
        ]
        tickets = []
        tranche_states: List[TrancheState] = []
        for name, lots, tp in half_specs:
            if lots < self.settings.lot_step:
                continue
            ticket = self.broker.open_market(
                symbol=self.settings.symbol, side=side, volume_lots=lots,
                sl=sl, tp=tp, comment=f"v3.2 {name}",
                magic=self.settings.magic_number,
                max_slippage_per_oz=self.settings.max_slippage_per_oz,
            )
            tickets.append(ticket)
            tranche_states.append(TrancheState(name=name, lots=lots))

        position = PositionState(
            direction=direction.value,
            entry_price=entry_price,
            entry_time_utc=now_utc,
            initial_stop=sl,
            tp1=tp1,
            tp2=tp2,
            tranches=tranche_states,
        )
        position.current_stop = sl
        position.running_extreme = entry_price
        position.intraday_high = entry_price
        position.intraday_low = entry_price

        self.state.position = position
        self.state.tickets = tickets
        self.state.daily_lock.mark_fired(self.state.today)
        self.state.week_trades += 1

        self.log.event("trade_open", {
            "direction": direction.value,
            "entry_kind": entry_kind,
            "entry_price": entry_price,
            "candle_body_ratio": entry_mod._body_ratio(confirmation_candle),
            "sl": sl, "tp1": tp1, "tp2": tp2,
            "tp2_fib": ctx.long_tp2_fib if direction == Direction.LONG else ctx.short_tp2_fib,
            "back_to_back_active": ctx.back_to_back_active,
            "high_atr_extension_active": ctx.high_atr_extension_active,
            **{k: v for k, v in asdict(sized).items()
               if k not in ("direction", "entry_price", "stop_loss")},
            "active_risk_pct": active_risk,
            "equity_at_open": equity,
            "tickets": [t.broker_id for t in tickets],
        })
        return True

    # ── Tick handler ──────────────────────────────────────
    def on_tick(self, tick_price: float, now_utc: datetime) -> None:
        pos = self.state.position
        if pos is None or pos.closed:
            return
        ctx = self.state.premarket
        if ctx is None:
            return

        # TP1 acceleration (Wednesday early, then standard)
        if exits.maybe_accelerate_tp1(ctx, pos, self.settings, now_utc):
            self.state.week_tp1_accels += 1
            if pos.accelerated_kind == "WEDNESDAY":
                self.state.week_wednesday_accels += 1
            self.log.event("tp1_accelerated", {
                "kind": pos.accelerated_kind,
                "new_tp1": pos.accelerated_tp1_price,
            })
            # propagate to broker ticket (H1)
            h1_ticket = next((t for t in self.state.tickets
                              if t.comment.endswith("H1")), None)
            if h1_ticket and pos.accelerated_tp1_price is not None:
                self.broker.modify_tp(h1_ticket, pos.accelerated_tp1_price)

        # 20:55 UTC partial close decision tree (v3.2)
        if FLAGS.OPT_SESSION_FORCE_CLOSE_2055:
            closed_2055 = exits.maybe_2055_force_close(ctx, pos, self.settings,
                                                         tick_price, now_utc)
            for t in closed_2055:
                self._record_close(t)
                if t.close_reason == CloseReason.SESSION_CLOSE_HALF2:
                    self.state.week_session_close_half2 += 1
                elif t.close_reason == CloseReason.SESSION_CLOSE_FULL_NO_TP1:
                    self.state.week_session_close_full += 1
            if closed_2055 and pos.closed:
                self._post_trade_bookkeeping(pos)
                return

        # 21:00 UTC safety net
        end_utc = session.session_end_utc(ctx.trade_date)
        if now_utc >= end_utc and not pos.closed:
            closed = exits.force_close_session_end(pos, tick_price, now_utc)
            for t in closed:
                self._record_close(t)
            self._post_trade_bookkeeping(pos)
            return

        # COMEX volume fade exit (Opt 1) — only after TP1
        if pos.tp1_hit and not pos.tp2_hit:
            h2 = exits.maybe_comex_volume_exit(pos, self.state.comex_tracker, now_utc)
            if h2 is not None and h2.is_open:
                h2.is_open = False
                h2.close_price = tick_price
                h2.close_reason = CloseReason.COMEX_VOL_FADE
                h2.close_time_utc = now_utc
                self.state.week_comex_vol_exits += 1
                self._record_close(h2)
                pos.closed = True
                self._post_trade_bookkeeping(pos)
                return

        update = exits.apply_tick(ctx, pos, self.settings, tick_price, now_utc)
        if update.stop_moved and update.new_stop is not None:
            for ticket in self.state.tickets:
                self.broker.modify_sl(ticket, update.new_stop)

        for closed_t in update.closed:
            self._record_close(closed_t)

        if pos.is_fully_closed or pos.closed:
            self._post_trade_bookkeeping(pos)

    # ── Opt 6: RSI trim hook (caller feeds post-TP1 15m closes) ──
    def feed_post_tp1_close(self, close_15m: float, now_utc: datetime) -> None:
        """Caller invokes this after each 15m candle closes once TP1 is hit.
        Triggers the one-shot RSI trim check."""
        pos = self.state.position
        if pos is None or not pos.tp1_hit or pos.rsi_trim_done or pos.closed:
            return
        self.state.closes_15m_post_tp1.append(close_15m)
        trim = exits.maybe_rsi_trim(pos, self.state.closes_15m_post_tp1,
                                     self.settings, now_utc)
        if trim is None:
            return
        # Partial-close on the H2 broker ticket
        h2_ticket = next((t for t in self.state.tickets
                           if t.comment.endswith("H2")), None)
        if h2_ticket is not None:
            try:
                self.broker.close(h2_ticket, trim["trim_lots"])
            except Exception as exc:
                self.log.warn(f"RSI trim partial close failed: {exc}")
        self.state.week_rsi_trims += 1
        self.log.event("rsi_post_tp1_trim", trim)

    # ── Close bookkeeping ─────────────────────────────────
    def _record_close(self, t: TrancheState) -> None:
        ticket = next((x for x in self.state.tickets if x.comment.endswith(t.name)), None)
        if ticket is not None and ticket.volume_lots > 0:
            try:
                self.broker.close(ticket, min(t.lots, ticket.volume_lots))
            except Exception as exc:
                self.log.warn(f"Broker close failed for {t.name}: {exc}")
        self.log.event("tranche_close", {
            "tranche": t.name, "lots": t.lots,
            "price": t.close_price, "reason": t.close_reason.value if t.close_reason else None,
            "time": t.close_time_utc,
        })

    def _post_trade_bookkeeping(self, pos: PositionState) -> None:
        any_tp1 = any(t.close_reason == CloseReason.TP1 for t in pos.tranches)
        any_tp2 = any(t.close_reason == CloseReason.TP2 for t in pos.tranches)
        regime_exit = any(t.close_reason == CloseReason.REGIME_OVERRIDE for t in pos.tranches)
        any_initial_sl = any(t.close_reason == CloseReason.INITIAL_SL for t in pos.tranches)

        self.state.win_rate_monitor.record(any_tp1)
        if any_tp1:
            self.state.week_wins_tp1 += 1
        if any_tp2:
            self.state.week_wins_tp2 += 1
        if regime_exit:
            self.state.week_regime_exits += 1
        if any_initial_sl and not any_tp1:
            self.state.week_sls += 1

        equity = self.broker.equity()
        self.state.update_peak(equity)
        self.state.loss_counter.record(
            was_loss=(any_initial_sl and not any_tp1),
            today=self.state.today,
            threshold=self.settings.consecutive_loss_pause,
        )
        self.state.circuit_breaker.check(equity, self.settings, datetime.utcnow())

        # v3.2: persist this trade's outcome for tomorrow's Opt 2 check
        outcome = pos.overall_close_type()
        if outcome is not None:
            self.state.prev_session_close_type = outcome

        self.state.position = None
        self.state.tickets = []
        self.state.closes_15m_post_tp1 = []

    def _record_gate_block(self, failures: List[str]) -> None:
        for f in failures:
            if f.startswith("G1: range") and "< ATR20" in f:
                self.state.gate_block_atr_floor += 1
            elif f.startswith("G1: range") and ">" in f:
                self.state.gate_block_atr_cap += 1
            elif f.startswith("G3:"):
                self.state.gate_block_event += 1
            elif f.startswith("G4: daily"):
                self.state.gate_block_daily_lock += 1
            elif f.startswith("G5:"):
                self.state.gate_block_trend += 1
            elif f.startswith("G6:"):
                self.state.gate_block_sma200 += 1


def _premarket_payload(ctx: PremarketContext) -> dict:
    return {
        "date": ctx.trade_date,
        "range": ctx.range,
        "atr_20": ctx.atr_20,
        "atr_50": ctx.atr_50,
        "sma200_daily": ctx.sma200_daily,
        "regime": ctx.regime,
        "regime_ratio": ctx.regime_ratio,
        "trend_bias": ctx.trend_bias,
        "long_entry": ctx.long_entry,
        "short_entry": ctx.short_entry,
        "long_tp1": ctx.long_tp1,
        "long_tp2": ctx.long_tp2,
        "long_tp2_fib": ctx.long_tp2_fib,
        "short_tp1": ctx.short_tp1,
        "short_tp2": ctx.short_tp2,
        "short_tp2_fib": ctx.short_tp2_fib,
        "filter_c_active": ctx.filter_c_active,
        "long_filter_f": ctx.long_filter_f,
        "short_filter_f": ctx.short_filter_f,
        "back_to_back_active": ctx.back_to_back_active,
        "high_atr_extension_active": ctx.high_atr_extension_active,
        "trail_distance_base": ctx.trail_distance_base,
        "session_start_utc": ctx.session_start_utc,
        "session_end_utc": ctx.session_end_utc,
        "event_blocked": ctx.event_blocked,
        "event_reason": ctx.event_reason,
        "seasonal_mult_long": ctx.seasonal_mult_long,
        "prev_session_close_type": ctx.prev_session_close_type,
    }

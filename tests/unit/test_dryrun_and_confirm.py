"""Tests for dry-run mode + confirm-trade gating."""
from __future__ import annotations

from dataclasses import replace
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from config.settings import Settings
from src.broker.adapter import OrderSide
from src.broker.dryrun_adapter import DryRunAdapter
from src.broker.paper_adapter import PaperAdapter
from src.data.feed import DataFeed
from src.engine.logger import StructuredLogger
from src.engine.runner import Engine
from src.strategy.entry import Candle, Direction
from tests.conftest import synthetic_daily, synthetic_h4
from webapp import create_app


def _long_zone_closes() -> list[float]:
    closes = [100.0]
    for i in range(20):
        delta = 0.4 if i % 2 == 0 else -0.35
        closes.append(closes[-1] + delta)
    return closes


class _StubFeed(DataFeed):
    def __init__(self, daily: pd.DataFrame, h4: pd.DataFrame):
        self._daily = daily
        self._h4 = h4

    def daily(self, symbol, end, lookback_days):
        return self._daily.tail(lookback_days)

    def h4(self, symbol, end_utc, lookback_bars):
        return self._h4.tail(lookback_bars)

    def m15(self, symbol, start_utc, end_utc):
        return pd.DataFrame()


def _confirmation_signal(ctx):
    rng = ctx.range
    confirmation = Candle(
        open=ctx.long_entry - rng * 0.05,
        high=ctx.long_entry + rng * 0.10,
        low=ctx.long_entry - rng * 0.06,
        close=ctx.long_entry + rng * 0.08,
    )
    next_candle = Candle(
        open=confirmation.close,
        high=confirmation.close + rng * 0.05,
        low=confirmation.close - rng * 0.01,
        close=confirmation.close + rng * 0.04,
    )
    return confirmation, next_candle


def _make_engine(tmp_path: Path, *, require_confirm: bool = False,
                 broker=None, base_price: float = 2000.0):
    trade_date = date(2024, 6, 25)
    daily = synthetic_daily(trade_date, n=220, base_price=base_price,
                             daily_range=30.0)
    daily.loc[daily.index[-1], "close"] = base_price + 80
    daily.loc[daily.index[-1], "high"] = base_price + 100
    daily.loc[daily.index[-1], "low"] = base_price + 55
    daily.loc[daily.index[-1], "open"] = base_price + 70
    h4 = synthetic_h4(datetime(trade_date.year, trade_date.month,
                                 trade_date.day, 13, 30, tzinfo=timezone.utc),
                       n=220, start_price=base_price)
    feed = _StubFeed(daily, h4)

    settings = Settings(
        mt5_login=0, mt5_password="", mt5_server="", mt5_terminal_path="",
        symbol="XAUUSD", starting_equity=100_000.0, risk_pct=0.03,
        magic_number=20260101,
        mode="paper", log_level="WARNING", log_dir=tmp_path / "logs",
        require_trade_confirmation=require_confirm,
    )
    if broker is None:
        broker = PaperAdapter(starting_equity=100_000.0, spread=0.10)
        broker.set_quote(mid=base_price + 80,
                          time_utc=datetime(trade_date.year, trade_date.month,
                                             trade_date.day, 13, 30,
                                             tzinfo=timezone.utc))
    logger = StructuredLogger(tmp_path / "logs", level="WARNING")
    engine = Engine(settings, broker, feed, logger)
    return engine, broker, settings, trade_date


# ── DryRunAdapter unit tests ─────────────────────────────────────────
class TestDryRunAdapter:
    def test_quote_passthrough(self):
        inner = PaperAdapter(starting_equity=50_000.0, spread=0.20)
        inner.set_quote(mid=2050.0)
        dr = DryRunAdapter(inner, symbol="XAUUSD")
        q = dr.quote("XAUUSD")
        assert q.bid == 2050.0 - 0.10
        assert q.ask == 2050.0 + 0.10

    def test_equity_balance_passthrough(self):
        inner = PaperAdapter(starting_equity=75_000.0)
        dr = DryRunAdapter(inner)
        assert dr.equity() == 75_000.0
        assert dr.balance() == 75_000.0

    def test_open_market_does_not_mutate_inner(self):
        inner = PaperAdapter(starting_equity=50_000.0, spread=0.20)
        inner.set_quote(mid=2000.0)
        dr = DryRunAdapter(inner, symbol="XAUUSD")
        ticket = dr.open_market(
            symbol="XAUUSD", side=OrderSide.BUY, volume_lots=0.5,
            sl=1990.0, tp=2020.0, comment="dryrun-test",
            magic=999, max_slippage_per_oz=0.30,
        )
        assert ticket.broker_id >= 900_000
        assert ticket.side == OrderSide.BUY
        assert ticket.volume_lots == 0.5
        # Inner has no real fill
        assert inner.fills == []
        assert inner._open == {}

    def test_close_simulated(self):
        inner = PaperAdapter(starting_equity=50_000.0, spread=0.20)
        inner.set_quote(mid=2000.0)
        dr = DryRunAdapter(inner, symbol="XAUUSD")
        ticket = dr.open_market(
            symbol="XAUUSD", side=OrderSide.BUY, volume_lots=1.0,
            sl=1990.0, tp=2020.0, comment="dryrun-test",
            magic=999, max_slippage_per_oz=0.30,
        )
        inner.set_quote(mid=2010.0)
        price = dr.close(ticket, volume_lots=1.0)
        # Inner bid at 2009.90
        assert price == pytest.approx(2009.90, abs=1e-6)
        # Inner equity unchanged — no real trade
        assert inner.equity() == 50_000.0

    def test_modify_sl_tp_simulated(self):
        inner = PaperAdapter(starting_equity=50_000.0, spread=0.20)
        inner.set_quote(mid=2000.0)
        dr = DryRunAdapter(inner, symbol="XAUUSD")
        ticket = dr.open_market(
            symbol="XAUUSD", side=OrderSide.SELL, volume_lots=0.2,
            sl=2010.0, tp=1990.0, comment="X",
            magic=1, max_slippage_per_oz=0.30,
        )
        assert dr.modify_sl(ticket, 2005.0)
        assert ticket.sl == 2005.0
        assert dr.modify_tp(ticket, 1985.0)
        assert ticket.tp == 1985.0

    def test_event_sink_invoked(self):
        events = []
        inner = PaperAdapter(starting_equity=50_000.0, spread=0.20)
        inner.set_quote(mid=2000.0)
        dr = DryRunAdapter(inner, symbol="XAUUSD",
                            event_sink=lambda k, p: events.append((k, p)))
        dr.open_market(
            symbol="XAUUSD", side=OrderSide.BUY, volume_lots=0.1,
            sl=1990.0, tp=2010.0, comment="X",
            magic=1, max_slippage_per_oz=0.30,
        )
        kinds = [e[0] for e in events]
        actions = [e[1]["action"] for e in events]
        assert kinds == ["dryrun_order"]
        assert actions == ["open_market"]


# ── Confirm-trade flow ───────────────────────────────────────────────
class TestPendingTradeFlow:
    def test_pending_trade_stages_no_orders(self, tmp_path):
        engine, broker, settings, td = _make_engine(
            tmp_path, require_confirm=True)
        ctx = engine.start_day(td)
        broker.set_quote(mid=ctx.long_entry + 0.05,
                          time_utc=ctx.session_start_utc + timedelta(minutes=30))
        conf, nxt = _confirmation_signal(ctx)
        fired = engine.evaluate_signal(
            now_utc=ctx.session_start_utc + timedelta(minutes=30),
            direction=Direction.LONG,
            confirmation_candle=conf, next_candle=nxt,
            closes_15m=_long_zone_closes(),
        )
        assert fired
        # No position opened — staged only
        assert engine.state.position is None
        assert engine.state.pending_trade is not None
        plan = engine.state.pending_trade
        assert plan.direction == "LONG"
        assert plan.sl == ctx.long_sl
        assert plan.tp1 == ctx.long_tp1
        # Daily lock NOT yet consumed
        assert not engine.state.daily_lock.has_fired(td)
        # Broker received no orders
        assert broker.fills == []

    def test_signals_blocked_while_pending(self, tmp_path):
        engine, broker, settings, td = _make_engine(
            tmp_path, require_confirm=True)
        ctx = engine.start_day(td)
        broker.set_quote(mid=ctx.long_entry + 0.05,
                          time_utc=ctx.session_start_utc + timedelta(minutes=30))
        conf, nxt = _confirmation_signal(ctx)
        engine.evaluate_signal(
            now_utc=ctx.session_start_utc + timedelta(minutes=30),
            direction=Direction.LONG, confirmation_candle=conf,
            next_candle=nxt, closes_15m=_long_zone_closes(),
        )
        assert engine.state.pending_trade is not None
        first_plan = engine.state.pending_trade
        # A second signal while the first is still pending must NOT fire —
        # use a small delta well inside the expiry window.
        fired2 = engine.evaluate_signal(
            now_utc=ctx.session_start_utc + timedelta(minutes=31),
            direction=Direction.LONG, confirmation_candle=conf,
            next_candle=nxt, closes_15m=_long_zone_closes(),
        )
        assert not fired2
        # Same plan still there
        assert engine.state.pending_trade is first_plan
        assert broker.fills == []

    def test_confirm_places_orders(self, tmp_path):
        engine, broker, settings, td = _make_engine(
            tmp_path, require_confirm=True)
        ctx = engine.start_day(td)
        broker.set_quote(mid=ctx.long_entry + 0.05,
                          time_utc=ctx.session_start_utc + timedelta(minutes=30))
        conf, nxt = _confirmation_signal(ctx)
        engine.evaluate_signal(
            now_utc=ctx.session_start_utc + timedelta(minutes=30),
            direction=Direction.LONG, confirmation_candle=conf,
            next_candle=nxt, closes_15m=_long_zone_closes(),
        )
        # Confirm
        placed = engine.confirm_pending_trade(
            now_utc=ctx.session_start_utc + timedelta(minutes=31))
        assert placed
        assert engine.state.pending_trade is None
        assert engine.state.position is not None
        # Orders reached the broker
        opens = [f for f in broker.fills if f["event"] == "open"]
        assert len(opens) >= 1
        assert engine.state.daily_lock.has_fired(td)

    def test_cancel_clears_pending(self, tmp_path):
        engine, broker, settings, td = _make_engine(
            tmp_path, require_confirm=True)
        ctx = engine.start_day(td)
        broker.set_quote(mid=ctx.long_entry + 0.05,
                          time_utc=ctx.session_start_utc + timedelta(minutes=30))
        conf, nxt = _confirmation_signal(ctx)
        engine.evaluate_signal(
            now_utc=ctx.session_start_utc + timedelta(minutes=30),
            direction=Direction.LONG, confirmation_candle=conf,
            next_candle=nxt, closes_15m=_long_zone_closes(),
        )
        assert engine.cancel_pending_trade()
        assert engine.state.pending_trade is None
        assert engine.state.position is None
        assert broker.fills == []
        # After cancel, daily lock NOT consumed → a new signal could fire
        assert not engine.state.daily_lock.has_fired(td)

    def test_expired_pending_auto_cancels(self, tmp_path):
        engine, broker, settings, td = _make_engine(
            tmp_path, require_confirm=True)
        # Tighten the timeout for the test
        engine.settings = replace(settings, pending_trade_max_age_seconds=10)
        ctx = engine.start_day(td)
        broker.set_quote(mid=ctx.long_entry + 0.05,
                          time_utc=ctx.session_start_utc + timedelta(minutes=30))
        conf, nxt = _confirmation_signal(ctx)
        t0 = ctx.session_start_utc + timedelta(minutes=30)
        engine.evaluate_signal(
            now_utc=t0, direction=Direction.LONG,
            confirmation_candle=conf, next_candle=nxt,
            closes_15m=_long_zone_closes(),
        )
        assert engine.state.pending_trade is not None
        # Move time well past expiry — next signal eval should clear and
        # then refuse to re-fire (daily-lock not consumed; let's see —
        # actually expire clears it, so a fresh signal CAN fire).
        far_future = t0 + timedelta(seconds=60)
        engine.evaluate_signal(
            now_utc=far_future, direction=Direction.LONG,
            confirmation_candle=conf, next_candle=nxt,
            closes_15m=_long_zone_closes(),
        )
        # The expired plan has been cleared; a new one was staged
        # because we have confirm-mode on.
        # If a new plan exists, the old expired one is gone.
        if engine.state.pending_trade is not None:
            assert engine.state.pending_trade.created_at_utc == far_future


# ── Webapp integration ───────────────────────────────────────────────
@pytest.fixture
def app(tmp_path):
    settings = Settings(
        mt5_login=0, mt5_password="", mt5_server="", mt5_terminal_path="",
        symbol="XAUUSD", starting_equity=100_000.0, risk_pct=0.03,
        magic_number=20260101,
        mode="paper", log_level="WARNING", log_dir=tmp_path / "logs",
        webapp_host="127.0.0.1", webapp_port=0,
        comex_webhook_enabled=False,
        require_trade_confirmation=False,
    )
    app = create_app(settings)
    app.config["TESTING"] = True
    return app


class TestWebappPendingTrade:
    def test_settings_get_includes_confirm_flag(self, app):
        client = app.test_client()
        r = client.get("/api/settings")
        assert r.status_code == 200
        data = r.get_json()
        assert "require_trade_confirmation" in data
        assert data["require_trade_confirmation"] is False

    def test_settings_post_toggles_confirm_flag(self, app, monkeypatch, tmp_path):
        # Redirect _save_env to write into tmp .env
        monkeypatch.chdir(tmp_path)
        client = app.test_client()
        r = client.post("/api/settings",
                         json={"require_trade_confirmation": True})
        assert r.status_code == 200
        # The new setting is reflected on a subsequent GET
        data = client.get("/api/settings").get_json()
        assert data["require_trade_confirmation"] is True

    def test_confirm_endpoint_409_when_no_pending(self, app):
        client = app.test_client()
        r = client.post("/api/control/confirm_trade")
        assert r.status_code == 409
        assert r.get_json()["ok"] is False

    def test_cancel_endpoint_409_when_no_pending(self, app):
        client = app.test_client()
        r = client.post("/api/control/cancel_trade")
        assert r.status_code == 409
        assert r.get_json()["ok"] is False

    def test_snapshot_carries_pending_trade(self, app, tmp_path):
        from src.engine.state import PendingTrade
        from src.strategy.sizing import SizingResult
        sup = app.config["SUPERVISOR"]
        # Build a tiny engine and inject a pending trade
        engine, broker, settings, td = _make_engine(
            tmp_path, require_confirm=True)
        engine.start_day(td)
        sized = SizingResult(
            direction="LONG", entry_price=2050.0, stop_loss=2040.0,
            sl_distance=10.0, risk_amount=3000.0, lots_raw=3.0, lots_base=3.0,
            seasonal_mult=1.0, alignment_mult=1.0, regime_mult=1.0,
            lots_final=3.0, tranche_1=1.5, tranche_2=1.5, tranche_3=0.0,
            actual_risk=2970.0, deviation_pct=1.0, warning=False,
        )
        now = datetime(2024, 6, 25, 13, 30, tzinfo=timezone.utc)
        engine.state.pending_trade = PendingTrade(
            direction="LONG", entry_kind="CONTINUATION",
            entry_price=2050.0, sl=2040.0, tp1=2070.0, tp2=2090.0,
            sizing=sized, equity_at_decision=100_000.0,
            active_risk_pct=0.03,
            created_at_utc=now,
            expires_at_utc=now + timedelta(seconds=300),
        )
        sup.engine = engine
        sup.broker = broker

        client = app.test_client()
        data = client.get("/api/status").get_json()
        assert data["pending_trade"] is not None
        pt = data["pending_trade"]
        assert pt["direction"] == "LONG"
        assert pt["entry_price"] == 2050.0
        assert pt["total_lots"] == 3.0
        assert "expires_at_utc" in pt

    def test_dashboard_template_has_pending_section(self, app):
        client = app.test_client()
        r = client.get("/")
        assert r.status_code == 200
        assert b"pending-trade-banner" in r.data
        assert b"Confirm" in r.data and b"place orders" in r.data

    def test_settings_template_has_confirm_toggle(self, app):
        client = app.test_client()
        r = client.get("/settings")
        assert r.status_code == 200
        assert b"require_trade_confirmation" in r.data
        assert b"Require operator confirmation" in r.data


# ── Algo kill switch ─────────────────────────────────────────────────
class TestAlgoKillSwitch:
    def test_signal_blocked_when_algo_disabled(self, tmp_path):
        engine, broker, settings, td = _make_engine(tmp_path)
        engine.settings = replace(settings, algo_enabled=False)
        ctx = engine.start_day(td)
        broker.set_quote(mid=ctx.long_entry + 0.05,
                          time_utc=ctx.session_start_utc + timedelta(minutes=30))
        conf, nxt = _confirmation_signal(ctx)
        fired = engine.evaluate_signal(
            now_utc=ctx.session_start_utc + timedelta(minutes=30),
            direction=Direction.LONG,
            confirmation_candle=conf, next_candle=nxt,
            closes_15m=_long_zone_closes(),
        )
        assert not fired
        assert engine.state.position is None
        assert broker.fills == []

    def test_signal_fires_when_re_enabled(self, tmp_path):
        engine, broker, settings, td = _make_engine(tmp_path)
        engine.settings = replace(settings, algo_enabled=False)
        ctx = engine.start_day(td)
        broker.set_quote(mid=ctx.long_entry + 0.05,
                          time_utc=ctx.session_start_utc + timedelta(minutes=30))
        conf, nxt = _confirmation_signal(ctx)
        # Disabled — no fire
        engine.evaluate_signal(
            now_utc=ctx.session_start_utc + timedelta(minutes=30),
            direction=Direction.LONG, confirmation_candle=conf,
            next_candle=nxt, closes_15m=_long_zone_closes(),
        )
        assert engine.state.position is None
        # Flip ON
        engine.settings = replace(engine.settings, algo_enabled=True)
        fired = engine.evaluate_signal(
            now_utc=ctx.session_start_utc + timedelta(minutes=31),
            direction=Direction.LONG, confirmation_candle=conf,
            next_candle=nxt, closes_15m=_long_zone_closes(),
        )
        assert fired
        assert engine.state.position is not None

    def test_confirm_pending_blocked_when_algo_disabled(self, tmp_path):
        engine, broker, settings, td = _make_engine(
            tmp_path, require_confirm=True)
        ctx = engine.start_day(td)
        broker.set_quote(mid=ctx.long_entry + 0.05,
                          time_utc=ctx.session_start_utc + timedelta(minutes=30))
        conf, nxt = _confirmation_signal(ctx)
        engine.evaluate_signal(
            now_utc=ctx.session_start_utc + timedelta(minutes=30),
            direction=Direction.LONG, confirmation_candle=conf,
            next_candle=nxt, closes_15m=_long_zone_closes(),
        )
        assert engine.state.pending_trade is not None
        # Flip kill switch OFF
        engine.settings = replace(engine.settings, algo_enabled=False)
        placed = engine.confirm_pending_trade(
            now_utc=ctx.session_start_utc + timedelta(minutes=31))
        assert not placed
        assert engine.state.pending_trade is None
        assert engine.state.position is None
        assert broker.fills == []


class TestAlgoToggleApi:
    def test_toggle_endpoint_flips_setting(self, app):
        client = app.test_client()
        r = client.post("/api/control/algo", json={"enabled": False})
        assert r.status_code == 200
        body = r.get_json()
        assert body["ok"] is True
        assert body["algo_enabled"] is False
        # Reflected in /api/settings
        data = client.get("/api/settings").get_json()
        assert data["algo_enabled"] is False
        # And /api/status
        snap = client.get("/api/status").get_json()
        assert snap["algo_enabled"] is False

    def test_toggle_endpoint_400_without_body(self, app):
        client = app.test_client()
        r = client.post("/api/control/algo", json={})
        assert r.status_code == 400

    def test_toggle_propagates_to_running_engine(self, app, tmp_path):
        sup = app.config["SUPERVISOR"]
        engine, broker, _settings, _td = _make_engine(tmp_path)
        sup.engine = engine
        sup.broker = broker
        client = app.test_client()
        r = client.post("/api/control/algo", json={"enabled": False})
        assert r.status_code == 200
        # Engine's own settings reference now sees the kill switch
        assert engine.settings.algo_enabled is False
        # Flip back
        client.post("/api/control/algo", json={"enabled": True})
        assert engine.settings.algo_enabled is True

    def test_sidebar_has_algo_toggle(self, app):
        """Sidebar lives in base.html so it shows on every page."""
        client = app.test_client()
        for path in ("/", "/settings", "/backtest", "/logs"):
            r = client.get(path)
            assert r.status_code == 200
            assert b'id="algo-toggle"' in r.data, f"missing on {path}"

    def test_settings_page_has_algo_toggle(self, app):
        client = app.test_client()
        r = client.get("/settings")
        assert b"algo_enabled" in r.data
        assert b"Algo enabled" in r.data

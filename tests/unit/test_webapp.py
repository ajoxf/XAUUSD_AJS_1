"""Flask webapp smoke tests."""
from __future__ import annotations

import pytest

from config.settings import Settings
from webapp import create_app


@pytest.fixture
def app(tmp_path):
    settings = Settings(
        mt5_login=0, mt5_password="", mt5_server="", mt5_terminal_path="",
        symbol="XAUUSD", starting_equity=100_000.0, risk_pct=0.03,
        magic_number=20260101,
        mode="paper", log_level="WARNING", log_dir=tmp_path / "logs",
        webapp_host="127.0.0.1", webapp_port=0,
        comex_webhook_enabled=False,
    )
    app = create_app(settings)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_dashboard_renders(client):
    r = client.get("/")
    assert r.status_code == 200
    assert b"Dashboard" in r.data


def test_settings_page_renders(client):
    r = client.get("/settings")
    assert r.status_code == 200
    assert b"Settings" in r.data
    assert b"FILTER_A_ATR_RANGE" in r.data
    assert b"OPT_1_COMEX_VOL_CONTINUATION" in r.data


def test_backtest_page_renders(client):
    r = client.get("/backtest")
    assert r.status_code == 200
    assert b"Run backtest" in r.data


def test_logs_page_renders(client):
    r = client.get("/logs")
    assert r.status_code == 200


def test_api_status(client):
    r = client.get("/api/status")
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] in ("stopped", "starting", "running", "completed", "error")
    assert "equity" in data
    assert "monitor" in data


def test_api_status_lite(client):
    r = client.get("/api/status/lite")
    assert r.status_code == 200
    data = r.get_json()
    assert "equity" in data
    assert "position_open" in data


def test_api_flags(client):
    r = client.get("/api/flags")
    assert r.status_code == 200
    flags = r.get_json()
    assert "OPT_1_COMEX_VOL_CONTINUATION" in flags
    assert flags["OPT_1_COMEX_VOL_CONTINUATION"] is True


def test_api_flag_toggle(client):
    r = client.post("/api/flags", json={"OPT_5_WEDNESDAY_ACCEL": False})
    assert r.status_code == 200
    assert r.get_json()["updated"]["OPT_5_WEDNESDAY_ACCEL"] is False
    client.post("/api/flags", json={"OPT_5_WEDNESDAY_ACCEL": True})


def test_settings_get(client):
    r = client.get("/api/settings")
    assert r.status_code == 200
    data = r.get_json()
    assert data["symbol"] == "XAUUSD"
    assert data["mode"] == "paper"
    # Auth fields removed in this build
    assert "admin_username" not in data
    assert "admin_password" not in data


def test_settings_dataclass_has_no_auth_fields():
    """Regression guard: auth was removed from Settings."""
    s = Settings.from_env()
    assert not hasattr(s, "admin_username")
    assert not hasattr(s, "admin_password")
    assert not hasattr(s, "secret_key")


def test_circuit_breaker_pct_env_configurable(monkeypatch):
    """CIRCUIT_BREAKER_PCT in .env overrides the default 0.30."""
    monkeypatch.setenv("CIRCUIT_BREAKER_PCT", "0.10")
    s = Settings.from_env()
    assert s.circuit_breaker_pct == 0.10


def test_circuit_breaker_pct_default(monkeypatch):
    monkeypatch.delenv("CIRCUIT_BREAKER_PCT", raising=False)
    s = Settings.from_env()
    assert s.circuit_breaker_pct == 0.30


def test_atr_periods_env_configurable(monkeypatch):
    """ATR_SHORT_PERIOD / ATR_LONG_PERIOD override the spec defaults."""
    monkeypatch.setenv("ATR_SHORT_PERIOD", "14")
    monkeypatch.setenv("ATR_LONG_PERIOD", "30")
    s = Settings.from_env()
    assert s.atr_short_period == 14
    assert s.atr_long_period == 30


def test_atr_periods_default(monkeypatch):
    monkeypatch.delenv("ATR_SHORT_PERIOD", raising=False)
    monkeypatch.delenv("ATR_LONG_PERIOD", raising=False)
    s = Settings.from_env()
    assert s.atr_short_period == 20
    assert s.atr_long_period == 50


def test_premarket_uses_custom_atr_periods():
    """Custom ATR periods change the calculated atr_20/atr_50 values."""
    from datetime import date, datetime, timezone
    from src.strategy import premarket
    from tests.conftest import synthetic_daily, synthetic_h4
    df = synthetic_daily(date(2024, 6, 25), n=220, daily_range=30.0)
    h4 = synthetic_h4(datetime(2024, 6, 25, 13, 30, tzinfo=timezone.utc), n=220)
    spec_ctx = premarket.build_premarket(date(2024, 6, 25), df, h4["close"])
    custom_ctx = premarket.build_premarket(
        date(2024, 6, 25), df, h4["close"],
        atr_short_period=10, atr_long_period=30,
    )
    assert spec_ctx.atr_short_period == 20
    assert custom_ctx.atr_short_period == 10
    assert custom_ctx.atr_long_period == 30
    # Different windows → different values
    assert spec_ctx.atr_20 != pytest.approx(custom_ctx.atr_20)


def test_premarket_rejects_invalid_atr_periods():
    from datetime import date, datetime, timezone
    from src.strategy import premarket
    from tests.conftest import synthetic_daily, synthetic_h4
    df = synthetic_daily(date(2024, 6, 25), n=220)
    h4 = synthetic_h4(datetime(2024, 6, 25, 13, 30, tzinfo=timezone.utc), n=220)
    # short >= long invalid
    with pytest.raises(ValueError, match="atr_short_period must be < atr_long_period"):
        premarket.build_premarket(date(2024, 6, 25), df, h4["close"],
                                    atr_short_period=50, atr_long_period=20)
    # period < 2 invalid
    with pytest.raises(ValueError, match="ATR periods must be ≥ 2"):
        premarket.build_premarket(date(2024, 6, 25), df, h4["close"],
                                    atr_short_period=1, atr_long_period=10)


def test_broker_balance_method_exists_on_all_adapters():
    from src.broker.paper_adapter import PaperAdapter
    p = PaperAdapter(starting_equity=50_000.0)
    assert p.balance() == 50_000.0
    assert p.equity() == 50_000.0


def test_snapshot_includes_balance_from_broker(app):
    from src.broker.paper_adapter import PaperAdapter
    sup = app.config["SUPERVISOR"]
    broker = PaperAdapter(starting_equity=75_000.0, spread=0.20)
    broker.set_quote(mid=2000.0)
    sup.broker = broker
    snap = sup.snapshot(recent_events_limit=0)
    assert snap.balance == 75_000.0
    assert snap.equity == 75_000.0
    payload = snap.to_dict()
    assert "balance" in payload
    assert payload["balance"] == 75_000.0


def test_paper_mode_does_not_eager_connect_trading_broker(app):
    """Paper mode does NOT create a trading broker eagerly — only on Start.
    The ticker stays `stale=True` on a freshly-loaded page."""
    sup = app.config["SUPERVISOR"]
    assert sup.broker is None


def test_paper_mode_reference_broker_when_mt5_present(app):
    """If MT5 is reachable, paper mode attaches a reference broker for
    balance display. Inject a fake broker to simulate that path."""
    from src.broker.paper_adapter import PaperAdapter
    sup = app.config["SUPERVISOR"]
    # Simulate a successful reference attach by injecting a stand-in
    ref = PaperAdapter(starting_equity=42_500.0)
    ref.set_quote(mid=2000.0)
    sup.reference_broker = ref
    snap = sup.snapshot(recent_events_limit=0)
    assert snap.balance == 42_500.0
    assert snap.equity == 42_500.0
    assert snap.balance_source == "MT5 reference · paper trading"


def test_paper_mode_falls_back_to_starting_equity_when_no_mt5(app):
    """No trading broker, no reference broker → fall back to settings."""
    sup = app.config["SUPERVISOR"]
    sup.broker = None
    sup.reference_broker = None
    snap = sup.snapshot(recent_events_limit=0)
    assert snap.balance == 100_000.0
    assert snap.balance_source == "paper · simulated"


def test_live_mode_broker_takes_priority_over_reference(app):
    """When the trading broker is the real MT5 (live mode), prefer it."""
    from dataclasses import replace
    from src.broker.paper_adapter import PaperAdapter
    sup = app.config["SUPERVISOR"]
    sup.settings = replace(sup.settings, mode="live")
    live = PaperAdapter(starting_equity=12_345.0)   # stand-in for MT5Adapter
    live.set_quote(mid=2000.0)
    sup.broker = live
    sup.reference_broker = PaperAdapter(starting_equity=99_999.0)
    snap = sup.snapshot(recent_events_limit=0)
    assert snap.balance == 12_345.0
    assert snap.balance_source == "live · MT5 account"


def test_live_mode_attempts_eager_connect(tmp_path, caplog):
    """Live mode tries to attach to MT5 on app init. The MetaTrader5 package
    isn't installed on Linux, so the attempt fails gracefully with a warning."""
    import logging
    caplog.set_level(logging.WARNING)
    s = Settings(
        mt5_login=0, mt5_password="", mt5_server="", mt5_terminal_path="",
        symbol="XAUUSD", starting_equity=100_000, risk_pct=0.03,
        magic_number=1, mode="live", log_level="WARNING",
        log_dir=tmp_path / "logs",
        webapp_host="127.0.0.1",
        comex_webhook_enabled=False,
    )
    app = create_app(s)
    sup = app.config["SUPERVISOR"]
    # On Linux without MT5, eager connect fails — broker stays None,
    # and a warning is logged. On Windows with MT5 running, broker would
    # be a connected MT5Adapter.
    assert sup.broker is None
    assert any("Eager MT5 pre-connect skipped" in r.message
                for r in caplog.records)


def test_backtest_handles_tz_aware_index(tmp_path):
    """Regression for pandas 2.x error:
    'Cannot pass a datetime or Timestamp with tzinfo with the tz parameter'.
    Backtest now normalises tz-aware cutoff against the h4 index timezone."""
    from datetime import date, datetime, timezone
    from dataclasses import replace
    from webapp.backtest import run_backtest
    from src.data.feed import DataFeed
    from tests.conftest import synthetic_daily, synthetic_h4
    import pandas as pd

    class _TzAwareFeed(DataFeed):
        def __init__(self):
            self._daily = synthetic_daily(date(2024, 7, 1), n=260)
            # 4H index is tz-aware (matches what yfinance returns for intraday)
            self._h4 = synthetic_h4(
                datetime(2024, 7, 1, 13, 30, tzinfo=timezone.utc), n=260)
            # The fixture index is already tz-aware via the timezone= datetimes
        def daily(self, sym, end, lookback_days):
            return self._daily.tail(lookback_days)
        def h4(self, sym, end_utc, lookback_bars):
            return self._h4.tail(lookback_bars)
        def m15(self, sym, s, e):
            return pd.DataFrame()

    settings = Settings(
        mt5_login=0, mt5_password="", mt5_server="", mt5_terminal_path="",
        symbol="XAUUSD", starting_equity=100_000.0, risk_pct=0.03,
        magic_number=1, mode="paper", log_level="WARNING",
        log_dir=tmp_path / "logs", comex_webhook_enabled=False,
    )
    feed = _TzAwareFeed()

    # Inject our stub feed by monkey-patching YFinanceFeed
    import webapp.backtest as bt_mod
    real = bt_mod.YFinanceFeed
    bt_mod.YFinanceFeed = lambda: feed
    try:
        # Should NOT raise "Cannot pass a datetime or Timestamp with tzinfo…"
        result = run_backtest(date(2024, 6, 20), date(2024, 6, 28), settings)
        assert result is not None
        # Doesn't matter whether trades happen — just that the tz comparison works
    finally:
        bt_mod.YFinanceFeed = real


def test_require_confirmation_env_configurable(monkeypatch):
    monkeypatch.setenv("REQUIRE_CONFIRMATION", "true")
    monkeypatch.setenv("CONFIRMATION_TIMEOUT_SEC", "45")
    s = Settings.from_env()
    assert s.require_confirmation is True
    assert s.confirmation_timeout_sec == 45


def test_require_confirmation_default_off(monkeypatch):
    monkeypatch.delenv("REQUIRE_CONFIRMATION", raising=False)
    s = Settings.from_env()
    assert s.require_confirmation is False
    assert s.confirmation_timeout_sec == 30


def test_pending_entry_view_in_snapshot(app):
    """When engine has a pending entry, snapshot exposes it for the dashboard."""
    from datetime import datetime, timedelta, timezone
    from src.engine.state import PendingEntry, EngineState
    from src.strategy.safety import (CircuitBreaker, ConsecutiveLossCounter,
                                       DailyTradeLock, WinRateMonitor)

    class _Stub:
        starting_equity = 100_000.0
        peak_equity = 100_000.0
    sup = app.config["SUPERVISOR"]

    # Build a minimal engine.state with a pending entry
    class _FakeEngine:
        def __init__(self):
            now = datetime.now(tz=timezone.utc)
            self.state = EngineState(
                starting_equity=100_000.0, peak_equity=100_000.0,
                win_rate_monitor=WinRateMonitor(base_risk=0.03),
                circuit_breaker=CircuitBreaker(starting_equity=100_000.0),
            )
            self.state.pending_entry = PendingEntry(
                direction="LONG", entry_price=2456.50, entry_kind="CONTINUATION",
                sl=2440.00, tp1=2472.00, tp2=2480.80,
                half_1_lots=1.02, half_2_lots=1.02,
                risk_amount=3000.0, actual_risk=3060.0, deviation_pct=2.0,
                sizing_audit={"lots_final": 2.04, "seasonal_mult": 1.0,
                                "alignment_mult": 1.0, "regime_mult": 1.0},
                created_at_utc=now,
                timeout_at_utc=now + timedelta(seconds=30),
            )

    sup.engine = _FakeEngine()
    snap = sup.snapshot(recent_events_limit=0)
    pe = snap.pending_entry
    assert pe is not None
    assert pe["direction"] == "LONG"
    assert pe["entry_price"] == 2456.50
    assert pe["half_1_lots"] == 1.02
    assert pe["remaining_seconds"] <= 30.0
    assert "timeout_at_utc" in pe


def test_confirm_and_cancel_trade_endpoints(app):
    """POST /api/control/confirm_trade and /cancel_trade exist and return ok flag."""
    client = app.test_client()
    # No pending entry → both return ok=False
    assert client.post("/api/control/confirm_trade").get_json()["ok"] is False
    assert client.post("/api/control/cancel_trade").get_json()["ok"] is False


def test_pending_entry_expires_after_timeout(app):
    """check_pending_expiry transitions pending → expired and clears it."""
    from datetime import datetime, timedelta, timezone
    from src.engine.state import PendingEntry, EngineState
    from src.engine.runner import Engine
    from src.engine.logger import StructuredLogger
    from src.broker.paper_adapter import PaperAdapter
    from src.data.feed import DataFeed
    import pandas as pd

    class _Feed(DataFeed):
        def daily(self, sym, end, lookback_days): return pd.DataFrame()
        def h4(self, sym, end_utc, lookback_bars): return pd.DataFrame()
        def m15(self, sym, s, e): return pd.DataFrame()

    s = app.config["SETTINGS"]
    logger = StructuredLogger(s.log_dir, level="WARNING")
    broker = PaperAdapter(starting_equity=100_000.0)
    engine = Engine(s, broker, _Feed(), logger)

    past = datetime.now(tz=timezone.utc) - timedelta(seconds=10)
    engine.state.pending_entry = PendingEntry(
        direction="LONG", entry_price=2000.0, entry_kind="X",
        sl=1990.0, tp1=2010.0, tp2=2020.0,
        half_1_lots=1.0, half_2_lots=1.0,
        risk_amount=1000.0, actual_risk=1000.0, deviation_pct=0.0,
        sizing_audit={"lots_final": 2.0, "seasonal_mult": 1.0,
                        "alignment_mult": 1.0, "regime_mult": 1.0},
        created_at_utc=past,
        timeout_at_utc=past + timedelta(seconds=1),   # already past
    )
    now = datetime.now(tz=timezone.utc)
    expired = engine.check_pending_expiry(now)
    assert expired is True
    assert engine.state.pending_entry is None


def test_dryrun_adapter_intercepts_writes_passes_reads():
    """DryRunAdapter logs writes but never delegates them; reads pass through."""
    from src.broker.dryrun_adapter import DryRunAdapter
    from src.broker.paper_adapter import PaperAdapter
    from src.broker.adapter import OrderSide
    underlying = PaperAdapter(starting_equity=50_000.0, spread=0.20)
    underlying.set_quote(mid=2000.0)
    dry = DryRunAdapter(underlying, symbol="XAUUSD")
    dry.connect()

    # Reads pass through
    assert dry.equity() == 50_000.0
    assert dry.balance() == 50_000.0
    q = dry.quote("XAUUSD")
    assert q.bid > 0 and q.ask > q.bid

    # Open: underlying equity should NOT change (no real order was placed)
    ticket = dry.open_market(
        symbol="XAUUSD", side=OrderSide.BUY, volume_lots=1.0,
        sl=1990.0, tp=2010.0, comment="test",
        magic=1, max_slippage_per_oz=0.30,
    )
    assert ticket.broker_id >= 900_000_000   # fake ID range
    assert dry.equity() == 50_000.0   # untouched

    # Close: should still leave underlying equity untouched
    underlying.set_quote(mid=2050.0)   # simulate price move
    price = dry.close(ticket, 1.0)
    assert price > 0
    assert dry.equity() == 50_000.0   # underlying never saw the close


def test_dashboard_renders_pending_entry_banner(client):
    """HTML ships the pending-entry section + Confirm/Cancel buttons."""
    r = client.get("/")
    assert r.status_code == 200
    assert b'id="pending-entry"' in r.data
    assert b"btn-confirm-trade" in r.data
    assert b"btn-cancel-trade" in r.data
    assert b"awaiting your approval" in r.data


def test_paper_mode_feed_is_yfinance(app):
    """Paper mode uses yfinance for historical replay."""
    from src.data.yfinance_feed import YFinanceFeed
    sup = app.config["SUPERVISOR"]
    feed = sup._make_feed()
    assert isinstance(feed, YFinanceFeed)


def test_live_mode_feed_falls_back_to_yfinance_without_mt5(tmp_path, caplog):
    """Live mode tries MT5DataFeed; on Linux without MT5 it falls back to
    yfinance and logs a loud warning about futures vs spot mis-alignment."""
    import logging
    from dataclasses import replace
    from src.data.yfinance_feed import YFinanceFeed
    caplog.set_level(logging.WARNING)
    s = Settings(
        mt5_login=0, mt5_password="", mt5_server="", mt5_terminal_path="",
        symbol="XAUUSD", starting_equity=100_000, risk_pct=0.03,
        magic_number=1, mode="live", log_level="WARNING",
        log_dir=tmp_path / "logs", comex_webhook_enabled=False,
    )
    app = create_app(s)
    sup = app.config["SUPERVISOR"]
    feed = sup._make_feed()
    # On Linux without MT5, MT5DataFeed import/construction fails → yfinance
    assert isinstance(feed, YFinanceFeed)
    assert any("futures prices differ" in r.message for r in caplog.records)


def test_ticker_includes_source_field(app):
    """Ticker payload reports which source the quote came from."""
    from src.broker.paper_adapter import PaperAdapter
    sup = app.config["SUPERVISOR"]
    broker = PaperAdapter(starting_equity=100_000.0, spread=0.20)
    broker.set_quote(mid=2000.0)
    sup.broker = broker
    data = app.test_client().get("/api/ticker").get_json()
    assert "source" in data
    # Paper mode trading broker → source 'paper'
    assert data["source"] in ("paper", "MT5")


def test_ticker_prefers_mt5_reference_in_paper(app):
    """In paper mode with an MT5 reference attached, the ticker reads MT5."""
    from src.broker.paper_adapter import PaperAdapter
    sup = app.config["SUPERVISOR"]
    # Trading broker = paper sim; reference = stand-in 'MT5'
    sup.broker = PaperAdapter(starting_equity=100_000.0, spread=0.50)
    sup.broker.set_quote(mid=1000.0)
    ref = PaperAdapter(starting_equity=100_000.0, spread=0.20)
    ref.set_quote(mid=2456.78)
    sup.reference_broker = ref
    q, source = sup.quote_for_display()
    assert source == "MT5"
    assert q.mid == pytest.approx(2456.78)


def test_manual_open_blocked_in_paper(app):
    """Manual orders are disabled in paper mode."""
    r = app.test_client().post("/api/orders/open",
                                json={"direction": "LONG", "lots": 0.1})
    data = r.get_json()
    assert data["ok"] is False
    assert "paper" in data["error"].lower()


def test_manual_open_bad_input_returns_400(app):
    r = app.test_client().post("/api/orders/open", json={"lots": 0.1})  # no direction
    assert r.status_code == 400
    assert r.get_json()["ok"] is False


def test_manual_close_endpoints_exist(app):
    client = app.test_client()
    # No position → close returns ok=False gracefully
    assert client.post("/api/orders/close").status_code == 200
    # close_all with no broker connected → ok False
    out = client.post("/api/orders/close_all").get_json()
    assert "ok" in out


def test_manual_open_in_dryrun_mode(tmp_path):
    """In dryrun mode with a (paper) broker injected, manual_open succeeds."""
    from dataclasses import replace
    from src.broker.paper_adapter import PaperAdapter
    s = Settings(
        mt5_login=0, mt5_password="", mt5_server="", mt5_terminal_path="",
        symbol="XAUUSD", starting_equity=100_000.0, risk_pct=0.03,
        magic_number=1, mode="dryrun", log_level="WARNING",
        log_dir=tmp_path / "logs", comex_webhook_enabled=False,
    )
    app = create_app(s)
    sup = app.config["SUPERVISOR"]
    broker = PaperAdapter(starting_equity=100_000.0, spread=0.20)
    broker.set_quote(mid=2000.0)
    sup.broker = broker
    result = sup.manual_open("LONG", 0.10, sl=1990.0, tp=2010.0)
    assert result["ok"] is True
    assert result["side"] == "BUY"
    assert len(sup.manual_tickets) == 1


def test_dashboard_has_manual_order_panel(client):
    r = client.get("/")
    assert b"Manual order" in r.data
    assert b"btn-manual-open" in r.data
    assert b"btn-flatten-all" in r.data
    assert b"btn-close-position" in r.data


def test_circuit_breaker_uses_configured_threshold(monkeypatch):
    """The CircuitBreaker safety check honours settings.circuit_breaker_pct."""
    from datetime import datetime, timezone
    from src.strategy.safety import CircuitBreaker

    monkeypatch.setenv("CIRCUIT_BREAKER_PCT", "0.10")
    s = Settings.from_env()
    cb = CircuitBreaker(starting_equity=100_000.0)
    now = datetime.now(tz=timezone.utc)
    # 8% drawdown — should NOT trigger at 10% threshold
    assert not cb.check(92_000.0, s, now)
    # 12% drawdown — should trigger
    assert cb.check(88_000.0, s, now)


def test_ticker_stale_when_no_broker(client):
    """Bot stopped → no broker connected → stale=True."""
    r = client.get("/api/ticker")
    assert r.status_code == 200
    data = r.get_json()
    assert data["stale"] is True
    assert data["symbol"] == "XAUUSD"
    assert "max_spread" in data
    assert data["bid"] is None and data["ask"] is None


def test_ticker_live_quotes(app):
    """Inject a paper broker into the supervisor and verify the ticker reads it."""
    from datetime import datetime, timezone
    from src.broker.paper_adapter import PaperAdapter
    broker = PaperAdapter(starting_equity=100_000.0, spread=0.30)
    broker.set_quote(mid=2456.78,
                      time_utc=datetime(2024, 6, 25, 14, 30, tzinfo=timezone.utc))
    sup = app.config["SUPERVISOR"]
    sup.broker = broker

    client = app.test_client()
    r = client.get("/api/ticker")
    assert r.status_code == 200
    data = r.get_json()
    assert data["stale"] is False
    assert data["bid"] == 2456.78 - 0.15    # mid - half_spread
    assert data["ask"] == 2456.78 + 0.15
    assert data["spread"] == pytest.approx(0.30, abs=1e-6)
    assert data["mid"] == pytest.approx(2456.78, abs=1e-6)
    assert "2024-06-25" in data["time"]


def test_dashboard_renders_active_levels_section(client):
    """The ATR + entry/SL/TP strip is wired into the page."""
    r = client.get("/")
    assert r.status_code == 200
    assert b"Active levels" in r.data
    assert b'id="levels-grid"' in r.data
    assert b'id="levels-source"' in r.data


def test_snapshot_carries_atr_and_levels(app):
    """premarket payload exposes everything the levels strip needs."""
    from datetime import date, datetime, timezone, timedelta
    from src.engine.runner import Engine
    from src.engine.logger import StructuredLogger
    from src.broker.paper_adapter import PaperAdapter
    from src.data.feed import DataFeed
    from tests.conftest import synthetic_daily, synthetic_h4

    class _Feed(DataFeed):
        def __init__(self, d, h):
            self._d = d; self._h = h
        def daily(self, sym, end, lookback_days): return self._d.tail(lookback_days)
        def h4(self, sym, end_utc, lookback_bars): return self._h.tail(lookback_bars)
        def m15(self, sym, s, e): return None

    settings = app.config["SETTINGS"]
    broker = PaperAdapter(starting_equity=100_000.0, spread=0.20)
    broker.set_quote(mid=2000.0)
    daily = synthetic_daily(date(2024, 6, 25), n=220, daily_range=30.0)
    h4 = synthetic_h4(datetime(2024, 6, 25, 13, 30, tzinfo=timezone.utc), n=220)
    logger = StructuredLogger(settings.log_dir, level="WARNING")
    engine = Engine(settings, broker, _Feed(daily, h4), logger)
    engine.start_day(date(2024, 6, 25))

    sup = app.config["SUPERVISOR"]
    sup.broker = broker
    sup.engine = engine

    snap = sup.snapshot(recent_events_limit=0)
    pm = snap.premarket
    assert pm is not None
    # ATR readings
    assert pm["atr_20"] > 0
    assert pm["atr_50"] > 0
    assert "regime" in pm and "regime_ratio" in pm
    # Trade levels — both directions
    for k in ("long_entry", "long_sl", "long_tp1", "long_tp2", "long_tp2_fib",
              "short_entry", "short_sl", "short_tp1", "short_tp2", "short_tp2_fib"):
        assert k in pm and pm[k] is not None
    assert pm["long_sl"] == pm["prev_close"]
    assert pm["long_tp1"] > pm["long_entry"]
    assert pm["short_tp1"] < pm["short_entry"]


def test_ticker_includes_max_spread_for_colour_coding(app):
    """Dashboard JS uses max_spread to colour-code green/yellow/red."""
    from src.broker.paper_adapter import PaperAdapter
    sup = app.config["SUPERVISOR"]
    sup.broker = PaperAdapter(starting_equity=100_000.0, spread=0.05)
    sup.broker.set_quote(mid=2000.0)

    client = app.test_client()
    data = client.get("/api/ticker").get_json()
    assert data["max_spread"] == 0.50    # default from Settings
    assert data["spread"] < data["max_spread"]   # green territory


def test_non_localhost_bind_just_warns(tmp_path, caplog):
    """Non-localhost bind no longer raises — just warns. User's choice."""
    import logging
    caplog.set_level(logging.WARNING)
    s = Settings(
        mt5_login=0, mt5_password="", mt5_server="", mt5_terminal_path="",
        symbol="XAUUSD", starting_equity=100_000, risk_pct=0.03,
        magic_number=1, mode="paper", log_level="WARNING",
        log_dir=tmp_path / "logs",
        webapp_host="0.0.0.0",
        comex_webhook_enabled=False,
    )
    app = create_app(s)
    assert app is not None
    assert any("exposes the bot beyond localhost" in r.message
                for r in caplog.records)

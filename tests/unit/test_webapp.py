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

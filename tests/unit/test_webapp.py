"""Flask webapp smoke tests."""
from __future__ import annotations

import base64

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
        admin_username="testuser", admin_password="testpass",
        secret_key="test-secret",
        comex_webhook_enabled=False,
    )
    app = create_app(settings)
    app.config["TESTING"] = True
    return app


@pytest.fixture
def client(app):
    return app.test_client()


def _basic_auth(user="testuser", pw="testpass"):
    token = base64.b64encode(f"{user}:{pw}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def test_health_unauthenticated(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.get_json()["ok"] is True


def test_dashboard_requires_auth(client):
    r = client.get("/")
    assert r.status_code == 401
    assert "WWW-Authenticate" in r.headers


def test_dashboard_with_auth(client):
    r = client.get("/", headers=_basic_auth())
    assert r.status_code == 200
    assert b"Dashboard" in r.data


def test_settings_page_renders(client):
    r = client.get("/settings", headers=_basic_auth())
    assert r.status_code == 200
    assert b"Settings" in r.data
    # Should include flag descriptions
    assert b"FILTER_A_ATR_RANGE" in r.data
    assert b"OPT_1_COMEX_VOL_CONTINUATION" in r.data


def test_backtest_page_renders(client):
    r = client.get("/backtest", headers=_basic_auth())
    assert r.status_code == 200
    assert b"Run backtest" in r.data


def test_logs_page_renders(client):
    r = client.get("/logs", headers=_basic_auth())
    assert r.status_code == 200


def test_api_status_requires_auth(client):
    r = client.get("/api/status")
    assert r.status_code == 401


def test_api_status_with_auth(client):
    r = client.get("/api/status", headers=_basic_auth())
    assert r.status_code == 200
    data = r.get_json()
    assert data["status"] in ("stopped", "starting", "running", "completed", "error")
    assert "equity" in data
    assert "monitor" in data


def test_api_status_lite(client):
    r = client.get("/api/status/lite", headers=_basic_auth())
    assert r.status_code == 200
    data = r.get_json()
    assert "equity" in data
    assert "position_open" in data


def test_api_flags(client):
    r = client.get("/api/flags", headers=_basic_auth())
    assert r.status_code == 200
    flags = r.get_json()
    assert "OPT_1_COMEX_VOL_CONTINUATION" in flags
    assert flags["OPT_1_COMEX_VOL_CONTINUATION"] is True


def test_api_flag_toggle(client):
    r = client.post("/api/flags", json={"OPT_5_WEDNESDAY_ACCEL": False},
                     headers=_basic_auth())
    assert r.status_code == 200
    assert r.get_json()["updated"]["OPT_5_WEDNESDAY_ACCEL"] is False
    # Reset
    client.post("/api/flags", json={"OPT_5_WEDNESDAY_ACCEL": True},
                 headers=_basic_auth())


def test_wrong_password_rejected(client):
    r = client.get("/api/status", headers=_basic_auth(pw="wrong"))
    assert r.status_code == 401


def test_settings_get(client):
    r = client.get("/api/settings", headers=_basic_auth())
    assert r.status_code == 200
    data = r.get_json()
    assert data["symbol"] == "XAUUSD"
    assert data["mode"] == "paper"


def test_refuses_public_bind_without_password(tmp_path):
    s = Settings(
        mt5_login=0, mt5_password="", mt5_server="", mt5_terminal_path="",
        symbol="XAUUSD", starting_equity=100_000, risk_pct=0.03,
        magic_number=1, mode="paper", log_level="WARNING",
        log_dir=tmp_path / "logs",
        webapp_host="0.0.0.0", admin_password="",
        comex_webhook_enabled=False,
    )
    with pytest.raises(RuntimeError, match="Refusing"):
        create_app(s)


@pytest.fixture
def open_app(tmp_path):
    """App with no password — auth fully disabled on localhost."""
    s = Settings(
        mt5_login=0, mt5_password="", mt5_server="", mt5_terminal_path="",
        symbol="XAUUSD", starting_equity=100_000.0, risk_pct=0.03,
        magic_number=20260101,
        mode="paper", log_level="WARNING", log_dir=tmp_path / "logs",
        webapp_host="127.0.0.1", admin_password="",
        comex_webhook_enabled=False,
    )
    app = create_app(s)
    app.config["TESTING"] = True
    return app


def test_open_localhost_no_password_skips_auth(open_app):
    """No ADMIN_PASSWORD set + localhost bind → no auth prompt, direct access."""
    c = open_app.test_client()
    r = c.get("/")
    assert r.status_code == 200
    assert b"Dashboard" in r.data
    r = c.get("/api/status")
    assert r.status_code == 200
    r = c.get("/api/flags")
    assert r.status_code == 200


def test_open_localhost_post_endpoints_also_open(open_app):
    """Mutating endpoints also open when no password — matches dashboard buttons."""
    c = open_app.test_client()
    r = c.post("/api/flags", json={"OPT_5_WEDNESDAY_ACCEL": True})
    assert r.status_code == 200

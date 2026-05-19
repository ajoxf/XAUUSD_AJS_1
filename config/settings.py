"""Runtime settings loaded from environment / .env."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


@dataclass
class Settings:
    # Broker
    mt5_login: int
    mt5_password: str
    mt5_server: str
    mt5_terminal_path: str

    # Trading
    symbol: str
    starting_equity: float
    risk_pct: float
    magic_number: int

    # Ops
    mode: str            # 'live' | 'paper' | 'dryrun'
    log_level: str
    log_dir: Path

    # Master kill switch for new entries. When False, the engine keeps
    # running so existing positions are still managed (trail, TP1/TP2,
    # session close), but signal evaluation is skipped so no NEW trades
    # are opened. Independent of Start/Stop — flipping this does NOT
    # tear down the engine thread.
    algo_enabled: bool = True

    # When True, the engine stages each trade as a pending decision and
    # waits for the operator to Confirm or Cancel from the dashboard
    # before any order is sent. Lapses to auto-cancel after
    # pending_trade_max_age_seconds.
    require_trade_confirmation: bool = False
    pending_trade_max_age_seconds: int = 300

    # Web UI
    webapp_host: str = "127.0.0.1"
    webapp_port: int = 8080

    # COMEX webhook
    comex_webhook_host: str = "0.0.0.0"
    comex_webhook_port: int = 5050
    comex_webhook_enabled: bool = True

    # Spec constants (override via env if needed)
    max_spread_per_oz: float = 0.50
    max_slippage_per_oz: float = 0.30
    min_range_floor: float = 10.0
    atr_short_period: int = 20      # fast ATR — range floor + Filter C
    atr_long_period: int = 50       # slow ATR — regime baseline
    atr_cap_multiplier: float = 1.8
    body_ratio_threshold: float = 0.60
    rsi_long_min: float = 45.0
    rsi_long_max: float = 65.0
    rsi_short_min: float = 35.0
    rsi_short_max: float = 55.0
    tranche_1_pct: float = 0.40
    tranche_2_pct: float = 0.30
    breakeven_plus_pct: float = 0.30
    tp1_accel_pct: float = 0.80
    circuit_breaker_pct: float = 0.30   # halt if equity drops this % from start
    consecutive_loss_pause: int = 7
    lot_step: float = 0.01
    contract_size: float = 100.0

    @classmethod
    def from_env(cls) -> "Settings":
        return cls(
            mt5_login=int(os.getenv("MT5_LOGIN", "0")),
            mt5_password=os.getenv("MT5_PASSWORD", ""),
            mt5_server=os.getenv("MT5_SERVER", ""),
            mt5_terminal_path=os.getenv("MT5_TERMINAL_PATH", ""),
            symbol=os.getenv("SYMBOL", "XAUUSD"),
            starting_equity=float(os.getenv("STARTING_EQUITY", "100000")),
            risk_pct=float(os.getenv("RISK_PCT", "0.03")),
            magic_number=int(os.getenv("MAGIC_NUMBER", "20260101")),
            mode=os.getenv("MODE", "paper").lower(),
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            log_dir=Path(os.getenv("LOG_DIR", "logs")),
            webapp_host=os.getenv("WEBAPP_HOST", "127.0.0.1"),
            webapp_port=int(os.getenv("WEBAPP_PORT", "8080")),
            comex_webhook_host=os.getenv("COMEX_WEBHOOK_HOST", "0.0.0.0"),
            comex_webhook_port=int(os.getenv("COMEX_WEBHOOK_PORT", "5050")),
            comex_webhook_enabled=os.getenv("COMEX_WEBHOOK_ENABLED",
                                              "true").lower() in ("1", "true", "yes"),
            circuit_breaker_pct=float(os.getenv("CIRCUIT_BREAKER_PCT", "0.30")),
            atr_short_period=int(os.getenv("ATR_SHORT_PERIOD", "20")),
            atr_long_period=int(os.getenv("ATR_LONG_PERIOD", "50")),
            algo_enabled=os.getenv("ALGO_ENABLED", "true").lower()
                in ("1", "true", "yes"),
            require_trade_confirmation=os.getenv(
                "REQUIRE_TRADE_CONFIRMATION", "false"
            ).lower() in ("1", "true", "yes"),
            pending_trade_max_age_seconds=int(
                os.getenv("PENDING_TRADE_MAX_AGE_SECONDS", "300")
            ),
        )

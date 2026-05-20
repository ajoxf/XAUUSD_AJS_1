"""Production launcher - Waitress WSGI + COMEX webhook server.

Usage:
    python -m webapp.server

The web UI binds to WEBAPP_HOST:WEBAPP_PORT (default 127.0.0.1:8080) with
no authentication. Keep it on localhost. For remote access, terminate at
a reverse proxy and put auth there.
"""
from __future__ import annotations

import logging
import signal
import sys
from typing import Optional

# Windows console default codec (cp1252) can't render some unicode chars
# we use in log messages (->, OK, >). Reconfigure stdout/stderr to UTF-8.
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
if hasattr(sys.stderr, "reconfigure"):
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from waitress import serve

from config.settings import Settings
from src.integrations.webhook_server import WebhookServer
from webapp import create_app

log = logging.getLogger("xauusd-bot.server")


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=getattr(logging, level, logging.INFO),
        format="%(asctime)sZ %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )


def main(argv: Optional[list[str]] = None) -> int:
    settings = Settings.from_env()
    _configure_logging(settings.log_level)

    app = create_app(settings)
    tracker = app.config.get("COMEX_TRACKER")
    webhook_server: Optional[WebhookServer] = None
    if tracker is not None:
        webhook_server = WebhookServer(tracker,
                                        host=settings.comex_webhook_host,
                                        port=settings.comex_webhook_port)
        try:
            webhook_server.start()
            log.info("COMEX webhook listening on http://%s:%d/comex_volume",
                      settings.comex_webhook_host, settings.comex_webhook_port)
        except OSError as exc:
            log.warning("Could not start COMEX webhook: %s", exc)
            webhook_server = None

    def _shutdown(*_args):
        log.info("Shutdown signal received")
        sup = app.config["SUPERVISOR"]
        sup.stop()
        if webhook_server:
            webhook_server.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    log.info("=" * 70)
    log.info("XAUUSD Fibonacci Range Bot v3.2")
    log.info("=" * 70)
    log.info("Mode:        %s", settings.mode.upper())
    log.info("Symbol:      %s", settings.symbol)
    log.info("Risk/trade:  %.2f%%", settings.risk_pct * 100)
    log.info("Circuit BR:  %.0f%%  (halts after this drawdown from start)",
              settings.circuit_breaker_pct * 100)
    if settings.mode == "live":
        mode_label = ("attach to running MT5 terminal"
                      if not settings.mt5_login
                      else f"login as account {settings.mt5_login}")
        log.info("Broker:      MT5 - %s", mode_label)
    else:
        log.info("Broker:      paper (yfinance replay)")
    log.info("Web UI:      http://%s:%d", settings.webapp_host, settings.webapp_port)
    log.info("Press > Start in the sidebar to begin trading.")
    log.info("=" * 70)
    serve(app, host=settings.webapp_host, port=settings.webapp_port,
          threads=8, ident="xauusd-bot/3.2")
    return 0


if __name__ == "__main__":
    sys.exit(main())

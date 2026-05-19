"""Production launcher — Waitress WSGI + COMEX webhook server.

Usage:
    python -m webapp.server
"""
from __future__ import annotations

import logging
import secrets
import signal
import sys
from typing import Optional

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

    if not settings.admin_password:
        if settings.webapp_host in ("127.0.0.1", "localhost"):
            log.warning("ADMIN_PASSWORD is empty — running on localhost without auth. "
                         "Set a password before exposing this port.")
        else:
            log.error("ADMIN_PASSWORD must be set for non-localhost binds. Aborting.")
            return 1

    if not settings.secret_key:
        log.warning("SECRET_KEY is empty — generating a random one for this run only. "
                     "Set SECRET_KEY in .env for persistent sessions.")
        settings = settings.__class__(**{**settings.__dict__,
                                          "secret_key": secrets.token_hex(32)})

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

    log.info("Starting webapp on http://%s:%d (mode=%s)",
              settings.webapp_host, settings.webapp_port, settings.mode)
    serve(app, host=settings.webapp_host, port=settings.webapp_port,
          threads=8, ident="xauusd-bot/3.2")
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Flask application factory."""
from __future__ import annotations

import logging
from typing import Optional

from flask import Flask

from config.settings import Settings
from src.strategy.comex_volume import ComexVolumeTracker
from webapp.supervisor import EngineSupervisor

log = logging.getLogger("xauusd-bot.webapp")


def create_app(settings: Optional[Settings] = None,
               supervisor: Optional[EngineSupervisor] = None) -> Flask:
    settings = settings or Settings.from_env()

    if settings.webapp_host not in ("127.0.0.1", "localhost"):
        log.warning(
            "WEBAPP_HOST=%s exposes the bot beyond localhost with no auth. "
            "Put a reverse proxy (nginx / Caddy / Cloudflare Tunnel) in front "
            "and gate access there.", settings.webapp_host,
        )

    app = Flask(__name__,
                template_folder="templates",
                static_folder="static")

    app.config["SETTINGS"] = settings
    app.config["JSON_SORT_KEYS"] = False
    app.config["TEMPLATES_AUTO_RELOAD"] = True

    tracker = ComexVolumeTracker() if settings.comex_webhook_enabled else None
    app.config["COMEX_TRACKER"] = tracker
    app.config["SUPERVISOR"] = supervisor or EngineSupervisor(settings,
                                                                comex_tracker=tracker)

    from webapp import views, api
    app.register_blueprint(views.bp)
    app.register_blueprint(api.bp, url_prefix="/api")

    @app.context_processor
    def inject_globals():
        return {"app_version": "3.2.0", "symbol": settings.symbol}

    return app

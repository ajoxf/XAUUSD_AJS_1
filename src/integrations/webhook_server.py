"""Minimal HTTP webhook receiver for TradingView COMEX volume alerts.

Listens on POST /comex_volume with JSON body:
    {"type":"volume_update","symbol":"GC1!","timeframe":"15",
     "volume":"<float>","time":"<iso8601>"}

Uses Python stdlib only - no Flask/FastAPI dependency. Runs in a daemon
thread so it can be embedded in the bot process.
"""
from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional

from src.strategy.comex_volume import ComexVolumeTracker

log = logging.getLogger("xauusd-bot.webhook")


def make_handler(tracker: ComexVolumeTracker):
    class _Handler(BaseHTTPRequestHandler):
        def _send(self, status: int, body: bytes = b"") -> None:
            self.send_response(status)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_POST(self) -> None:
            if self.path != "/comex_volume":
                self._send(404)
                return
            length = int(self.headers.get("Content-Length", "0"))
            try:
                raw = self.rfile.read(length).decode("utf-8")
                data = json.loads(raw)
                volume = float(data["volume"])
                ts_raw = data.get("time")
                ts = _parse_time(ts_raw) if ts_raw else datetime.now(tz=timezone.utc)
                triggered = tracker.update(volume, ts)
                log.info("COMEX volume update: %.0f (streak=%d, exit=%s)",
                          volume, tracker.consecutive_weak, triggered)
                self._send(200, b'{"ok":true}')
            except (KeyError, ValueError, json.JSONDecodeError) as exc:
                log.warning("Bad COMEX webhook payload: %s", exc)
                self._send(400, b'{"ok":false}')

        def do_GET(self) -> None:
            if self.path == "/health":
                self._send(200, b'{"ok":true}')
                return
            self._send(404)

        def log_message(self, *args, **kwargs) -> None:
            # Suppress stdlib's default access log - we log via the bot logger
            pass

    return _Handler


def _parse_time(s: str) -> datetime:
    """Parse common timestamp formats (TradingView sends epoch or ISO)."""
    s = s.strip()
    if s.isdigit():
        return datetime.fromtimestamp(int(s) / 1000.0
                                       if len(s) > 10 else int(s),
                                       tz=timezone.utc)
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return datetime.now(tz=timezone.utc)


class WebhookServer:
    def __init__(self, tracker: ComexVolumeTracker, host: str = "0.0.0.0",
                 port: int = 5050):
        self.tracker = tracker
        self.host = host
        self.port = port
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        handler = make_handler(self.tracker)
        self._server = ThreadingHTTPServer((self.host, self.port), handler)
        self._thread = threading.Thread(target=self._server.serve_forever,
                                         daemon=True, name="comex-webhook")
        self._thread.start()
        log.info("COMEX webhook listening on http://%s:%s/comex_volume",
                  self.host, self.port)

    def stop(self) -> None:
        if self._server is not None:
            self._server.shutdown()
            self._server.server_close()
            self._server = None

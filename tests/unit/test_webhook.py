"""HTTP webhook receiver smoke tests."""
import json
import threading
import time
import urllib.request
from urllib.error import HTTPError

import pytest

from src.integrations.webhook_server import WebhookServer
from src.strategy.comex_volume import ComexVolumeTracker


@pytest.fixture
def server():
    tracker = ComexVolumeTracker()
    srv = WebhookServer(tracker, host="127.0.0.1", port=5099)
    srv.start()
    time.sleep(0.1)
    yield srv, tracker
    srv.stop()


def _post(url: str, payload: dict) -> int:
    req = urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"}, method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=2) as resp:
            return resp.status
    except HTTPError as exc:
        return exc.code


def test_health_endpoint(server):
    _srv, _tracker = server
    with urllib.request.urlopen("http://127.0.0.1:5099/health", timeout=2) as resp:
        assert resp.status == 200


def test_comex_volume_update(server):
    _srv, tracker = server
    code = _post("http://127.0.0.1:5099/comex_volume",
                 {"type": "volume_update", "symbol": "GC1!",
                  "timeframe": "15", "volume": "123.45",
                  "time": "2024-06-25T14:00:00Z"})
    assert code == 200
    assert tracker.latest_volume() == 123.45


def test_bad_payload_returns_400(server):
    _srv, _tracker = server
    code = _post("http://127.0.0.1:5099/comex_volume", {"foo": "bar"})
    assert code == 400


def test_unknown_path_returns_404(server):
    code = _post("http://127.0.0.1:5099/nope", {"x": 1})
    assert code == 404

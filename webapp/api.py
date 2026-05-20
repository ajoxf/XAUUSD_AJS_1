"""JSON API: bot control, snapshot, settings, backtest, SSE stream."""
from __future__ import annotations

import json
import time
from dataclasses import asdict, replace
from datetime import date, datetime
from pathlib import Path
from typing import Generator

from flask import (Blueprint, Response, current_app, jsonify, request,
                    stream_with_context)

from config.flags import FLAGS, FeatureFlags
from config.settings import Settings
from webapp.backtest import run_backtest
from webapp.supervisor import EngineSupervisor

bp = Blueprint("api", __name__)


def _supervisor() -> EngineSupervisor:
    return current_app.config["SUPERVISOR"]


def _settings() -> Settings:
    return current_app.config["SETTINGS"]


# ── Status / snapshot ───────────────────────────────────
@bp.get("/status")
def status():
    sup = _supervisor()
    snap = sup.snapshot(recent_events_limit=30)
    return jsonify(snap.to_dict())


@bp.get("/status/lite")
def status_lite():
    """Minimal payload for high-frequency polling fallback."""
    sup = _supervisor()
    snap = sup.snapshot(recent_events_limit=0)
    return jsonify({
        "status": snap.status, "mode": snap.mode,
        "equity": snap.equity, "drawdown_pct": snap.drawdown_pct,
        "today": snap.today.isoformat() if snap.today else None,
        "position_open": snap.position is not None,
    })


# ── SSE stream ──────────────────────────────────────────
@bp.get("/stream")
def stream():
    """Server-Sent Events: snapshot pushed every ~2 seconds while connected."""
    sup = _supervisor()

    @stream_with_context
    def generate() -> Generator[str, None, None]:
        last_payload = ""
        keepalive = 0
        while True:
            try:
                snap = sup.snapshot(recent_events_limit=20)
                payload = json.dumps(snap.to_dict(), default=str)
                if payload != last_payload:
                    yield f"event: snapshot\ndata: {payload}\n\n"
                    last_payload = payload
                    keepalive = 0
                else:
                    keepalive += 1
                    if keepalive >= 15:    # ~30s of no change → ping
                        yield ": keepalive\n\n"
                        keepalive = 0
            except GeneratorExit:
                return
            except Exception as exc:
                yield f"event: error\ndata: {json.dumps({'error': str(exc)})}\n\n"
            time.sleep(2.0)

    resp = Response(generate(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


# ── Control ─────────────────────────────────────────────
@bp.post("/control/start")
def start_bot():
    sup = _supervisor()
    sup.start()
    return jsonify({"status": sup.status, "is_running": sup.is_running})


@bp.post("/control/stop")
def stop_bot():
    sup = _supervisor()
    sup.stop()
    return jsonify({"status": sup.status, "is_running": sup.is_running})


@bp.post("/control/confirm_trade")
def confirm_trade():
    """Approve a pending entry. Bot sends the order to the broker now."""
    sup = _supervisor()
    ok = sup.confirm_pending()
    return jsonify({"ok": ok})


@bp.post("/control/cancel_trade")
def cancel_trade():
    """Reject a pending entry. No order is sent; daily lock NOT fired so the
    bot can take a later signal next day."""
    sup = _supervisor()
    ok = sup.cancel_pending()
    return jsonify({"ok": ok})


# ── Manual order controls ───────────────────────────────
@bp.post("/orders/open")
def manual_open():
    """Manually open a market order (live/dryrun only). Body:
    {direction: LONG|SHORT, lots: float, sl?: float, tp?: float}"""
    sup = _supervisor()
    data = request.get_json(silent=True) or request.form.to_dict()
    try:
        direction = str(data["direction"])
        lots = float(data["lots"])
    except (KeyError, ValueError) as exc:
        return jsonify({"ok": False, "error": f"bad input: {exc}"}), 400
    sl = float(data["sl"]) if data.get("sl") not in (None, "", "0") else None
    tp = float(data["tp"]) if data.get("tp") not in (None, "", "0") else None
    result = sup.manual_open(direction, lots, sl, tp)
    return jsonify(result), (200 if result.get("ok") else 400)


@bp.post("/orders/close")
def manual_close():
    """Close the bot's current strategy position at market."""
    sup = _supervisor()
    return jsonify(sup.manual_close_strategy())


@bp.post("/orders/close_all")
def manual_close_all():
    """Flatten everything carrying our magic number - strategy + manual."""
    sup = _supervisor()
    return jsonify(sup.manual_close_all())


# ── Settings ────────────────────────────────────────────
@bp.get("/settings")
def get_settings():
    s = _settings()
    return jsonify({
        "symbol": s.symbol, "starting_equity": s.starting_equity,
        "risk_pct": s.risk_pct, "magic_number": s.magic_number,
        "mode": s.mode, "log_level": s.log_level,
        "log_dir": str(s.log_dir),
        "mt5_login": s.mt5_login, "mt5_server": s.mt5_server,
        "mt5_terminal_path": s.mt5_terminal_path,
        "webapp_host": s.webapp_host, "webapp_port": s.webapp_port,
    })


@bp.post("/settings")
def update_settings():
    s = _settings()
    data = request.get_json(silent=True) or request.form.to_dict()
    new = replace(
        s,
        symbol=str(data.get("symbol", s.symbol)),
        starting_equity=float(data.get("starting_equity", s.starting_equity)),
        risk_pct=float(data.get("risk_pct", s.risk_pct)),
        magic_number=int(data.get("magic_number", s.magic_number)),
        mode=str(data.get("mode", s.mode)),
        log_level=str(data.get("log_level", s.log_level)),
        mt5_login=int(data.get("mt5_login", s.mt5_login) or 0),
        mt5_password=str(data.get("mt5_password", s.mt5_password) or s.mt5_password),
        mt5_server=str(data.get("mt5_server", s.mt5_server)),
        mt5_terminal_path=str(data.get("mt5_terminal_path", s.mt5_terminal_path)),
    )
    _save_env(Path(".env"), {
        "SYMBOL": new.symbol, "STARTING_EQUITY": new.starting_equity,
        "RISK_PCT": new.risk_pct, "MAGIC_NUMBER": new.magic_number,
        "MODE": new.mode, "LOG_LEVEL": new.log_level,
        "MT5_LOGIN": new.mt5_login, "MT5_PASSWORD": new.mt5_password,
        "MT5_SERVER": new.mt5_server, "MT5_TERMINAL_PATH": new.mt5_terminal_path,
    })
    current_app.config["SETTINGS"] = new
    sup = _supervisor()
    sup.update_settings(new)
    return jsonify({"ok": True})


def _save_env(path: Path, updates: dict) -> None:
    existing: dict[str, str] = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            existing[k.strip()] = v.strip()
    existing.update({str(k): str(v) for k, v in updates.items()})
    path.write_text("\n".join(f"{k}={v}" for k, v in existing.items()) + "\n")


# ── Feature flags ───────────────────────────────────────
@bp.get("/flags")
def get_flags():
    return jsonify({k: getattr(FLAGS, k) for k in vars(FeatureFlags()).keys()})


@bp.post("/flags")
def update_flags():
    data = request.get_json(silent=True) or request.form.to_dict()
    updated = {}
    for k, v in data.items():
        if hasattr(FLAGS, k):
            val = v if isinstance(v, bool) else str(v).lower() in ("1", "true", "yes", "on")
            setattr(FLAGS, k, val)
            updated[k] = val
    return jsonify({"updated": updated})


# ── Backtest ────────────────────────────────────────────
@bp.post("/backtest")
def backtest():
    data = request.get_json(silent=True) or request.form.to_dict()
    s = _settings()
    try:
        start = date.fromisoformat(data["start"])
        end = date.fromisoformat(data["end"])
        starting_equity = float(data.get("starting_equity", s.starting_equity))
    except (KeyError, ValueError) as exc:
        return jsonify({"error": f"Bad input: {exc}"}), 400

    bt_settings = replace(s, starting_equity=starting_equity)
    try:
        result = run_backtest(start, end, bt_settings)
    except Exception as exc:
        return jsonify({"error": f"Backtest failed: {exc}"}), 500
    return jsonify({
        "starting_equity": result.starting_equity,
        "final_equity": result.final_equity,
        "n_trades": result.n_trades,
        "tp1_win_rate": round(result.tp1_win_rate, 2),
        "total_return_pct": round(result.total_return_pct, 2),
        "max_drawdown_pct": round(result.max_drawdown_pct, 2),
        "total_pnl": round(result.total_pnl, 2),
        "blocked_days": result.blocked_days,
        "no_signal_days": result.no_signal_days,
        "skipped_days": result.skipped_days,
        "equity_curve": [{"date": d.isoformat(), "equity": eq}
                          for d, eq in result.equity_curve],
        "trades": [
            {"date": t.trade_date.isoformat(), "direction": t.direction,
             "regime": t.regime, "entry": t.entry_price,
             "sl": t.sl, "tp1": t.tp1, "tp2": t.tp2, "lots": t.lots,
             "outcome": t.outcome, "exit_price": t.exit_price,
             "pnl": t.pnl, "equity_after": t.equity_after}
            for t in result.trades
        ],
    })


# ── Logs ────────────────────────────────────────────────
@bp.get("/logs")
def get_logs():
    s = _settings()
    path = s.log_dir / "events.jsonl"
    if not path.exists():
        return jsonify({"events": []})
    kind_filter = request.args.get("kind", "").strip()
    limit = int(request.args.get("limit", 200))
    with path.open() as f:
        lines = f.readlines()
    events = []
    for ln in reversed(lines):
        if not ln.strip():
            continue
        try:
            e = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if kind_filter and e.get("kind") != kind_filter:
            continue
        events.append(e)
        if len(events) >= limit:
            break
    return jsonify({"events": list(reversed(events))})


# ── Live ticker ─────────────────────────────────────────
@bp.get("/ticker")
def ticker():
    """Current bid / ask / spread. Polled at 300ms from the dashboard.

    Returns `stale=True` if no broker is connected (bot stopped) or if the
    broker call fails. Includes `max_spread` so the client can colour-code.
    """
    sup = _supervisor()
    s = _settings()
    payload = {
        "symbol": s.symbol,
        "max_spread": s.max_spread_per_oz,
        "stale": True, "source": "none",
        "bid": None, "ask": None, "spread": None, "mid": None,
        "time": None, "error": None,
    }
    q, source = sup.quote_for_display()
    payload["source"] = source
    if q is None:
        payload["error"] = "no quote source (start the bot or MT5)"
        return jsonify(payload)
    payload.update({
        "bid": q.bid, "ask": q.ask, "spread": q.spread, "mid": q.mid,
        "time": q.time_utc.isoformat(),
        "stale": False,
    })
    return jsonify(payload)


# ── Health ──────────────────────────────────────────────
@bp.get("/health")
def health():
    return jsonify({"ok": True, "ts": datetime.utcnow().isoformat() + "Z"})

"""Structured JSONL logger — spec §9.

Writes structured events to logs/events.jsonl and a human-readable
summary to logs/bot.log AND to the console (via the root logger which
the webapp/server.py configures with a StreamHandler).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict


class StructuredLogger:
    def __init__(self, log_dir: Path, level: str = "INFO"):
        log_dir.mkdir(parents=True, exist_ok=True)
        self.log_dir = log_dir
        self.text_log = log_dir / "bot.log"
        self.events_log = log_dir / "events.jsonl"

        self.log = logging.getLogger("xauusd-bot")
        self.log.setLevel(getattr(logging, level, logging.INFO))

        # File handler — append, attach only once
        already_attached = any(
            isinstance(h, logging.FileHandler)
            and getattr(h, "baseFilename", "") == str(self.text_log.resolve())
            for h in self.log.handlers
        )
        if not already_attached:
            fh = logging.FileHandler(str(self.text_log))
            fh.setFormatter(logging.Formatter(
                "%(asctime)sZ %(levelname)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S",
            ))
            self.log.addHandler(fh)

        # propagate=True so server.py's stdout StreamHandler also catches us
        self.log.propagate = True

    # ── Events ───────────────────────────────────────────
    def event(self, kind: str, payload: Dict[str, Any]) -> None:
        """Append a JSON record to events.jsonl, plus a human-readable
        one-liner via the logger (stdout + bot.log)."""
        record = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "kind": kind,
            **payload,
        }
        with self.events_log.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")
        self.log.info("[%s] %s", kind, _summarize_event(kind, payload))

    def warn(self, msg: str, **payload: Any) -> None:
        if payload:
            self.log.warning("%s %s", msg, json.dumps(payload, default=str))
        else:
            self.log.warning(msg)

    def info(self, msg: str, **payload: Any) -> None:
        if payload:
            self.log.info("%s %s", msg, json.dumps(payload, default=str))
        else:
            self.log.info(msg)


def _fmt_money(v: Any) -> str:
    try:
        return f"${float(v):.2f}"
    except (TypeError, ValueError):
        return "—"


def _summarize_event(kind: str, p: Dict[str, Any]) -> str:
    """One-line, human-readable summary of an event for stdout + bot.log."""
    if kind == "premarket":
        return (f"day={p.get('date')} range={_fmt_money(p.get('range'))} "
                f"ATR20={_fmt_money(p.get('atr_20'))} "
                f"regime={p.get('regime')} bias={p.get('trend_bias')} "
                f"long_entry={_fmt_money(p.get('long_entry'))} "
                f"TP1={_fmt_money(p.get('long_tp1'))} "
                f"TP2={_fmt_money(p.get('long_tp2'))} "
                f"event_blocked={p.get('event_blocked')}")
    if kind == "trade_open":
        return (f"{p.get('direction')} @ {_fmt_money(p.get('entry_price'))} "
                f"SL={_fmt_money(p.get('sl'))} "
                f"TP1={_fmt_money(p.get('tp1'))} "
                f"TP2={_fmt_money(p.get('tp2'))} "
                f"lots={p.get('lots_final')} "
                f"risk={_fmt_money(p.get('actual_risk'))} "
                f"({p.get('entry_kind')})")
    if kind == "tranche_close":
        return (f"{p.get('tranche')} closed @ {_fmt_money(p.get('price'))} "
                f"({p.get('reason')}) lots={p.get('lots')}")
    if kind == "gate_block":
        fails = p.get("failures") or []
        return f"{p.get('direction')} blocked: {'; '.join(fails)}"
    if kind == "entry_reject":
        return f"Layer {p.get('layer')} rejected: {', '.join(p.get('reasons', []))}"
    if kind == "tp1_accelerated":
        return f"TP1 accelerated to {_fmt_money(p.get('new_tp1'))} ({p.get('kind')})"
    if kind == "rsi_post_tp1_trim":
        return (f"RSI trim: {p.get('trim_lots')} lots at RSI {p.get('rsi_at_trim')} "
                f"(H2 remaining {p.get('remaining_h2_lots')})")
    # Generic fallback — short truncated kv dump
    return ", ".join(f"{k}={v}" for k, v in p.items() if k != "ts")[:200]

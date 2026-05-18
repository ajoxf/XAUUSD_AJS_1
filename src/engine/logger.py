"""Structured JSONL logger — spec §9."""
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
        logging.basicConfig(
            filename=str(self.text_log),
            level=getattr(logging, level, logging.INFO),
            format="%(asctime)sZ %(levelname)s %(message)s",
            datefmt="%Y-%m-%dT%H:%M:%S",
        )
        self.log = logging.getLogger("xauusd-bot")

    def event(self, kind: str, payload: Dict[str, Any]) -> None:
        record = {
            "ts": datetime.now(tz=timezone.utc).isoformat(),
            "kind": kind,
            **payload,
        }
        with self.events_log.open("a") as f:
            f.write(json.dumps(record, default=str) + "\n")
        self.log.info("%s %s", kind, json.dumps(payload, default=str))

    def warn(self, msg: str, **payload: Any) -> None:
        self.log.warning("%s %s", msg, json.dumps(payload, default=str) if payload else "")

    def info(self, msg: str, **payload: Any) -> None:
        self.log.info("%s %s", msg, json.dumps(payload, default=str) if payload else "")

"""CLI entry point.

Usage:
    python -m src.main premarket            # compute & print today's context
    python -m src.main live                  # start live trading loop (MT5)
    python -m src.main paper                 # paper-trading loop (synthetic ticks)

The live loop expects MT5 to be initialised and the symbol to be visible.
Strategy state is rebuilt fresh each day at 00:05 UTC.
"""
from __future__ import annotations

import argparse
import sys
import time
from datetime import date, datetime, timezone

from config.settings import Settings
from src.broker.mt5_adapter import MT5Adapter
from src.broker.paper_adapter import PaperAdapter
from src.data.yfinance_feed import YFinanceFeed
from src.engine.logger import StructuredLogger
from src.engine.runner import Engine
from src.strategy import session


def _make_broker(settings: Settings):
    if settings.mode == "live":
        return MT5Adapter(settings)
    return PaperAdapter(starting_equity=settings.starting_equity)


def cmd_premarket(settings: Settings, logger: StructuredLogger) -> int:
    broker = _make_broker(settings)
    broker.connect()
    feed = YFinanceFeed()
    engine = Engine(settings, broker, feed, logger)
    today = datetime.now(tz=timezone.utc).date()
    ctx = engine.start_day(today)
    broker.disconnect()
    if ctx is None:
        print("Pre-market build failed — see logs", file=sys.stderr)
        return 1
    print(f"Pre-market for {ctx.trade_date}:")
    print(f"  range={ctx.range:.2f} atr20={ctx.atr_20:.2f} regime={ctx.regime}")
    print(f"  trend_bias={ctx.trend_bias} event_blocked={ctx.event_blocked}")
    print(f"  long: entry={ctx.long_entry:.2f} tp1={ctx.long_tp1:.2f} tp2={ctx.long_tp2:.2f}")
    print(f"  short: entry={ctx.short_entry:.2f} tp1={ctx.short_tp1:.2f} tp2={ctx.short_tp2:.2f}")
    print(f"  session: {ctx.session_start_utc} → {ctx.session_end_utc} UTC")
    return 0


def cmd_live(settings: Settings, logger: StructuredLogger) -> int:
    """Bare-bones live loop. Production deployment should wrap this in a
    supervisor (systemd/Windows Task Scheduler) that restarts on crash."""
    broker = _make_broker(settings)
    broker.connect()
    feed = YFinanceFeed()
    engine = Engine(settings, broker, feed, logger)

    current_day: date | None = None
    last_15m_tick: datetime | None = None

    try:
        while True:
            now = datetime.now(tz=timezone.utc)
            if now.date() != current_day:
                current_day = now.date()
                engine.start_day(current_day)
                last_15m_tick = None

            ctx = engine.state.premarket
            if ctx is None:
                time.sleep(60)
                continue

            # Tick-level position management
            if engine.state.position is not None and not engine.state.position.closed:
                quote = broker.quote(settings.symbol)
                engine.on_tick(quote.mid, now)

            # 15-minute candle close detection (simplified: real implementation
            # should align to exchange clock and pull confirmed bars from MT5)
            minute_align = now.minute % 15 == 0 and now.second < 5
            if minute_align and (last_15m_tick is None or
                                 (now - last_15m_tick).total_seconds() > 60):
                last_15m_tick = now
                # In a full implementation we'd fetch the just-closed 15m bar
                # from MT5 and run signal evaluation. Out of scope: see
                # tests/integration/test_end_to_end.py for the deterministic
                # version that exercises the same code path.
                logger.info("15m boundary — signal scan stub (see runner docs)")

            time.sleep(5)
    except KeyboardInterrupt:
        logger.info("Shutdown requested")
    finally:
        broker.disconnect()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="xauusd-bot")
    parser.add_argument("command", choices=["premarket", "live", "paper"])
    args = parser.parse_args(argv)

    settings = Settings.from_env()
    if args.command == "paper":
        settings.mode = "paper"
    elif args.command == "live":
        settings.mode = "live"

    logger = StructuredLogger(settings.log_dir, level=settings.log_level)
    logger.info(f"Starting xauusd-bot v3.0 in {settings.mode} mode")

    if args.command == "premarket":
        return cmd_premarket(settings, logger)
    return cmd_live(settings, logger)


if __name__ == "__main__":
    sys.exit(main())

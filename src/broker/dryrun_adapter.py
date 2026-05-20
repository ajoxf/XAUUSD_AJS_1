"""Dry-run broker adapter - wraps another broker, intercepts writes.

In `MODE=dryrun` the bot computes everything (gates, sizing, entries, exits)
but every order-affecting call (open_market, modify_sl, modify_tp, close)
is logged and never sent to the real broker. Reads (quote, equity, balance,
open_tickets) pass through to the wrapped broker.

Used to validate live MT5 mechanics without risking real money.
"""
from __future__ import annotations

import itertools
import logging
from datetime import datetime, timezone
from typing import List, Optional

from src.broker.adapter import BrokerAdapter, OrderSide, OrderTicket, TickQuote

log = logging.getLogger("xauusd-bot.dryrun")


class DryRunAdapter(BrokerAdapter):
    """Read-through to `wrapped`; write operations are no-ops with logs."""

    _id_counter = itertools.count(start=900_000_001)

    def __init__(self, wrapped: BrokerAdapter, symbol: str = "XAUUSD"):
        self.wrapped = wrapped
        self.symbol = symbol
        self._open_tickets: List[OrderTicket] = []

    # ── pass-through ─────────────────────────────────────
    def connect(self) -> None:
        self.wrapped.connect()
        log.info("DRYRUN adapter active - orders will be simulated only")

    def disconnect(self) -> None:
        self.wrapped.disconnect()

    def equity(self) -> float:
        return self.wrapped.equity()

    def balance(self) -> float:
        return self.wrapped.balance()

    def quote(self, symbol: str) -> TickQuote:
        return self.wrapped.quote(symbol)

    def account_summary(self) -> dict:
        summary = dict(self.wrapped.account_summary())
        summary["dryrun"] = True
        return summary

    # ── intercepted writes ──────────────────────────────
    def open_market(self, symbol, side, volume_lots, sl, tp, comment, magic,
                    max_slippage_per_oz) -> OrderTicket:
        q = self.wrapped.quote(symbol)
        fill_price = q.ask if side == OrderSide.BUY else q.bid
        fake_id = next(DryRunAdapter._id_counter)
        log.warning(
            "[DRYRUN] OPEN %s %s %.2f lots @ %.2f (sl=%.2f tp=%s) magic=%d cmt=%s",
            side.value, symbol, volume_lots, fill_price, sl,
            f"{tp:.2f}" if tp is not None else "None", magic, comment,
        )
        ticket = OrderTicket(
            broker_id=fake_id, side=side, volume_lots=float(volume_lots),
            open_price=float(fill_price), sl=float(sl),
            tp=float(tp) if tp is not None else None,
            comment=comment,
            opened_at_utc=datetime.now(tz=timezone.utc),
        )
        self._open_tickets.append(ticket)
        return ticket

    def modify_sl(self, ticket: OrderTicket, new_sl: float) -> bool:
        log.warning("[DRYRUN] MODIFY SL ticket=%d %.2f -> %.2f",
                     ticket.broker_id, ticket.sl, new_sl)
        ticket.sl = float(new_sl)
        return True

    def modify_tp(self, ticket: OrderTicket, new_tp: float) -> bool:
        log.warning("[DRYRUN] MODIFY TP ticket=%d %s -> %.2f",
                     ticket.broker_id,
                     f"{ticket.tp:.2f}" if ticket.tp is not None else "None", new_tp)
        ticket.tp = float(new_tp)
        return True

    def close(self, ticket: OrderTicket, volume_lots: float) -> float:
        q = self.wrapped.quote(self.symbol)
        close_price = q.bid if ticket.side == OrderSide.BUY else q.ask
        per_oz = (close_price - ticket.open_price) if ticket.side == OrderSide.BUY \
            else (ticket.open_price - close_price)
        pnl = per_oz * float(volume_lots) * 100.0
        log.warning(
            "[DRYRUN] CLOSE ticket=%d %.2f lots @ %.2f (pnl=$%.2f, simulated)",
            ticket.broker_id, volume_lots, close_price, pnl,
        )
        remaining = ticket.volume_lots - float(volume_lots)
        if remaining <= 1e-9:
            self._open_tickets = [t for t in self._open_tickets
                                    if t.broker_id != ticket.broker_id]
        else:
            ticket.volume_lots = remaining
        return close_price

    def open_tickets(self, symbol: str, magic: int) -> List[OrderTicket]:
        # Return our simulated open tickets that match the magic
        return [t for t in self._open_tickets if t.comment]

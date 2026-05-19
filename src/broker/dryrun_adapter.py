"""Dry-run broker — wraps another adapter, simulates order placement.

Used when MODE=dryrun. Quotes, equity and balance come from the wrapped
adapter (typically a real MT5 connection so the bot sees real prices);
order-placement methods (open_market / modify_sl / modify_tp / close)
log the intent and return synthesised tickets without ever touching the
broker. The engine therefore exercises every code path that live mode
does — sizing, gates, exits, bookkeeping — without putting capital at
risk.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from itertools import count
from typing import List, Optional

from src.broker.adapter import BrokerAdapter, OrderSide, OrderTicket, TickQuote

log = logging.getLogger("xauusd-bot.dryrun")


class DryRunAdapter(BrokerAdapter):
    """Read-only passthrough + simulated mutations.

    The inner adapter is asked for everything that doesn't change broker
    state (connect/disconnect, quote, equity, balance, open_tickets).
    Anything that would change state is intercepted and logged."""

    _id_seq = count(900_000)

    def __init__(self, inner: BrokerAdapter, symbol: str = "XAUUSD",
                 event_sink=None):
        self.inner = inner
        self.symbol = symbol
        self.event_sink = event_sink   # optional callable(kind, payload) for structured logging
        self._simulated: dict[int, OrderTicket] = {}

    # ── connection ───────────────────────────────────────
    def connect(self) -> None:
        self.inner.connect()

    def disconnect(self) -> None:
        self.inner.disconnect()

    # ── read-through ─────────────────────────────────────
    def equity(self) -> float:
        return self.inner.equity()

    def balance(self) -> float:
        return self.inner.balance()

    def quote(self, symbol: str) -> TickQuote:
        return self.inner.quote(symbol)

    def open_tickets(self, symbol: str, magic: int) -> List[OrderTicket]:
        # Dry-run pretends the bot's own simulated tickets are open.
        # The inner adapter's positions are irrelevant — those belong to
        # the human / other strategies.
        return [t for t in self._simulated.values()]

    # ── simulated mutations ──────────────────────────────
    def open_market(
        self, symbol: str, side: OrderSide, volume_lots: float,
        sl: float, tp: Optional[float], comment: str, magic: int,
        max_slippage_per_oz: float,
    ) -> OrderTicket:
        quote = self.inner.quote(symbol)
        price = quote.ask if side == OrderSide.BUY else quote.bid
        oid = next(self._id_seq)
        ticket = OrderTicket(
            broker_id=oid, side=side, volume_lots=float(volume_lots),
            open_price=float(price), sl=float(sl), tp=tp,
            comment=comment, opened_at_utc=datetime.now(tz=timezone.utc),
        )
        self._simulated[oid] = ticket
        log.info(
            "DRYRUN open_market simulated: %s %.2f lots %s @ %.2f sl=%.2f tp=%s (%s)",
            side.value, volume_lots, symbol, price, sl,
            f"{tp:.2f}" if tp is not None else "—", comment,
        )
        self._emit("dryrun_order", {
            "action": "open_market", "symbol": symbol, "side": side.value,
            "volume_lots": volume_lots, "price": price, "sl": sl, "tp": tp,
            "comment": comment, "magic": magic, "broker_id": oid,
        })
        return ticket

    def modify_sl(self, ticket: OrderTicket, new_sl: float) -> bool:
        log.info("DRYRUN modify_sl simulated: ticket=%d sl %.2f → %.2f",
                  ticket.broker_id, ticket.sl, new_sl)
        ticket.sl = float(new_sl)
        if ticket.broker_id in self._simulated:
            self._simulated[ticket.broker_id].sl = float(new_sl)
        self._emit("dryrun_order", {
            "action": "modify_sl", "broker_id": ticket.broker_id,
            "new_sl": new_sl,
        })
        return True

    def modify_tp(self, ticket: OrderTicket, new_tp: float) -> bool:
        log.info("DRYRUN modify_tp simulated: ticket=%d tp → %.2f",
                  ticket.broker_id, new_tp)
        ticket.tp = float(new_tp)
        if ticket.broker_id in self._simulated:
            self._simulated[ticket.broker_id].tp = float(new_tp)
        self._emit("dryrun_order", {
            "action": "modify_tp", "broker_id": ticket.broker_id,
            "new_tp": new_tp,
        })
        return True

    def close(self, ticket: OrderTicket, volume_lots: float) -> float:
        try:
            q = self.inner.quote(self.symbol)
            price = q.bid if ticket.side == OrderSide.BUY else q.ask
        except Exception:
            price = ticket.open_price

        remaining = max(0.0, ticket.volume_lots - float(volume_lots))
        if remaining <= 1e-9:
            self._simulated.pop(ticket.broker_id, None)
        else:
            ticket.volume_lots = remaining
            if ticket.broker_id in self._simulated:
                self._simulated[ticket.broker_id].volume_lots = remaining

        log.info(
            "DRYRUN close simulated: ticket=%d volume=%.2f @ %.2f (remaining %.2f)",
            ticket.broker_id, volume_lots, price, remaining,
        )
        self._emit("dryrun_order", {
            "action": "close", "broker_id": ticket.broker_id,
            "volume_lots": volume_lots, "price": price,
            "remaining_lots": remaining,
        })
        return float(price)

    def _emit(self, kind: str, payload: dict) -> None:
        if self.event_sink is None:
            return
        try:
            self.event_sink(kind, payload)
        except Exception as exc:
            log.debug("dryrun event_sink failed: %s", exc)



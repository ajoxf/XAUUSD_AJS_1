"""In-memory paper broker. Used by tests and `MODE=paper`."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Dict, List, Optional

from src.broker.adapter import BrokerAdapter, OrderSide, OrderTicket, TickQuote


class PaperAdapter(BrokerAdapter):
    def __init__(self, starting_equity: float, spread: float = 0.20):
        self._equity = starting_equity
        self.spread = spread
        self._last_bid: float = 0.0
        self._last_ask: float = 0.0
        self._last_time: datetime = datetime.now(tz=timezone.utc)
        self._next_id: int = 100000
        self._open: Dict[int, OrderTicket] = {}
        self._closed: List[OrderTicket] = []
        self.fills: List[Dict] = []

    def set_quote(self, mid: float, time_utc: Optional[datetime] = None) -> None:
        half = self.spread / 2.0
        self._last_bid = mid - half
        self._last_ask = mid + half
        self._last_time = time_utc or datetime.now(tz=timezone.utc)

    def adjust_equity(self, delta: float) -> None:
        self._equity += delta

    # ── BrokerAdapter ────────────────────────────────────
    def connect(self) -> None:
        pass

    def disconnect(self) -> None:
        pass

    def equity(self) -> float:
        return self._equity

    def balance(self) -> float:
        # Paper sim only updates _equity on close; treat balance == equity.
        return self._equity

    def account_summary(self) -> dict:
        return {"connected": False, "kind": "paper"}

    def quote(self, symbol: str) -> TickQuote:
        return TickQuote(self._last_bid, self._last_ask, self._last_time)

    def open_market(self, symbol, side, volume_lots, sl, tp, comment, magic,
                    max_slippage_per_oz) -> OrderTicket:
        price = self._last_ask if side == OrderSide.BUY else self._last_bid
        oid = self._next_id
        self._next_id += 1
        ticket = OrderTicket(
            broker_id=oid, side=side, volume_lots=volume_lots,
            open_price=price, sl=sl, tp=tp, comment=comment,
            opened_at_utc=self._last_time,
        )
        self._open[oid] = ticket
        self.fills.append({"event": "open", "ticket": oid, "side": side.value,
                           "volume": volume_lots, "price": price,
                           "sl": sl, "tp": tp})
        return ticket

    def modify_sl(self, ticket, new_sl) -> bool:
        if ticket.broker_id not in self._open:
            return False
        ticket.sl = new_sl
        self._open[ticket.broker_id].sl = new_sl
        self.fills.append({"event": "modify_sl", "ticket": ticket.broker_id, "sl": new_sl})
        return True

    def modify_tp(self, ticket, new_tp) -> bool:
        if ticket.broker_id not in self._open:
            return False
        ticket.tp = new_tp
        self._open[ticket.broker_id].tp = new_tp
        self.fills.append({"event": "modify_tp", "ticket": ticket.broker_id, "tp": new_tp})
        return True

    def close(self, ticket, volume_lots) -> float:
        if ticket.broker_id not in self._open:
            raise RuntimeError("Unknown ticket")
        price = self._last_bid if ticket.side == OrderSide.BUY else self._last_ask
        pnl_per_oz = (price - ticket.open_price) if ticket.side == OrderSide.BUY \
            else (ticket.open_price - price)
        pnl = pnl_per_oz * volume_lots * 100.0
        self._equity += pnl
        # Partial vs full
        remaining = ticket.volume_lots - volume_lots
        if remaining <= 1e-9:
            del self._open[ticket.broker_id]
            self._closed.append(ticket)
        else:
            ticket.volume_lots = remaining
            self._open[ticket.broker_id].volume_lots = remaining
        self.fills.append({"event": "close", "ticket": ticket.broker_id,
                           "volume": volume_lots, "price": price, "pnl": pnl})
        return price

    def open_tickets(self, symbol, magic) -> List[OrderTicket]:
        return list(self._open.values())

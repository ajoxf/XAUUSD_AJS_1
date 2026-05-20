"""Broker abstraction. MT5 in production, PaperAdapter for tests."""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import List, Optional


class OrderSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


@dataclass
class OrderTicket:
    broker_id: int
    side: OrderSide
    volume_lots: float
    open_price: float
    sl: float
    tp: Optional[float]
    comment: str
    opened_at_utc: datetime


@dataclass
class TickQuote:
    bid: float
    ask: float
    time_utc: datetime

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2.0

    @property
    def spread(self) -> float:
        return self.ask - self.bid


class BrokerAdapter(ABC):
    @abstractmethod
    def connect(self) -> None: ...

    @abstractmethod
    def disconnect(self) -> None: ...

    @abstractmethod
    def equity(self) -> float:
        """Current account equity = balance + unrealised P&L on open positions."""

    @abstractmethod
    def balance(self) -> float:
        """Realised account balance - does not move while a trade is open."""

    @abstractmethod
    def quote(self, symbol: str) -> TickQuote: ...

    @abstractmethod
    def open_market(
        self, symbol: str, side: OrderSide, volume_lots: float,
        sl: float, tp: Optional[float], comment: str, magic: int,
        max_slippage_per_oz: float,
    ) -> OrderTicket: ...

    @abstractmethod
    def modify_sl(self, ticket: OrderTicket, new_sl: float) -> bool: ...

    @abstractmethod
    def modify_tp(self, ticket: OrderTicket, new_tp: float) -> bool: ...

    @abstractmethod
    def close(self, ticket: OrderTicket, volume_lots: float) -> float:
        """Returns close price."""

    @abstractmethod
    def open_tickets(self, symbol: str, magic: int) -> List[OrderTicket]: ...

"""MetaTrader 5 broker adapter. Requires `MetaTrader5` package + running terminal."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Optional

from config.settings import Settings
from src.broker.adapter import BrokerAdapter, OrderSide, OrderTicket, TickQuote

try:
    import MetaTrader5 as mt5
except ImportError:
    mt5 = None


class MT5Adapter(BrokerAdapter):
    def __init__(self, settings: Settings):
        if mt5 is None:
            raise ImportError(
                "MetaTrader5 package not available. Install with `pip install MetaTrader5`. "
                "Note: this package only runs on Windows or under Wine."
            )
        self.settings = settings
        self._connected = False

    # ── connection ───────────────────────────────────────
    def connect(self) -> None:
        """Connect to MT5.

        Attach mode (MT5_LOGIN blank or 0): attaches to whatever MT5 terminal
        is currently running and logged in. The bot never sees the password.
        Recommended for personal/desktop use.

        Credential mode (MT5_LOGIN set): bot logs in itself using the stored
        credentials. Required for headless/remote setups.
        """
        path = self.settings.mt5_terminal_path or None
        login = self.settings.mt5_login

        if not login:
            # Attach mode — defer all auth to the running terminal
            init_args = {}
            if path:
                init_args["path"] = path
            ok = mt5.initialize(**init_args)
            if not ok:
                err = mt5.last_error()
                raise RuntimeError(
                    "MT5 attach mode failed — is the MT5 terminal running and "
                    f"logged in? Error: {err}. Set MT5_LOGIN/MT5_PASSWORD/"
                    "MT5_SERVER in .env to log in via the bot instead."
                )
            account = mt5.account_info()
            if account is None:
                mt5.shutdown()
                raise RuntimeError(
                    "MT5 attached but no account is logged in. Open MT5 and "
                    "log in to your broker, then restart the bot."
                )
        else:
            # Credential mode — bot performs the login
            init_args = {"login": login,
                         "password": self.settings.mt5_password,
                         "server": self.settings.mt5_server}
            if path:
                init_args["path"] = path
            ok = mt5.initialize(**init_args)
            if not ok:
                err = mt5.last_error()
                raise RuntimeError(
                    f"MT5 login failed for account {login} on "
                    f"{self.settings.mt5_server}: {err}"
                )

        info = mt5.symbol_info(self.settings.symbol)
        if info is None:
            mt5.shutdown()
            raise RuntimeError(
                f"Symbol '{self.settings.symbol}' not found on MT5. Check the "
                "exact symbol name in Market Watch and update SYMBOL in .env."
            )
        if not info.visible:
            mt5.symbol_select(self.settings.symbol, True)
        self._connected = True

    def disconnect(self) -> None:
        if self._connected:
            mt5.shutdown()
            self._connected = False

    # ── account ──────────────────────────────────────────
    def equity(self) -> float:
        info = mt5.account_info()
        if info is None:
            raise RuntimeError("MT5 account_info() returned None")
        return float(info.equity)

    def quote(self, symbol: str) -> TickQuote:
        tick = mt5.symbol_info_tick(symbol)
        if tick is None:
            raise RuntimeError(f"MT5 returned no tick for {symbol}")
        return TickQuote(
            bid=float(tick.bid), ask=float(tick.ask),
            time_utc=datetime.fromtimestamp(tick.time, tz=timezone.utc),
        )

    # ── orders ───────────────────────────────────────────
    def open_market(
        self, symbol: str, side: OrderSide, volume_lots: float,
        sl: float, tp: Optional[float], comment: str, magic: int,
        max_slippage_per_oz: float,
    ) -> OrderTicket:
        order_type = mt5.ORDER_TYPE_BUY if side == OrderSide.BUY else mt5.ORDER_TYPE_SELL
        tick = mt5.symbol_info_tick(symbol)
        price = tick.ask if side == OrderSide.BUY else tick.bid
        info = mt5.symbol_info(symbol)
        # MT5 deviation is in points. For XAUUSD typically 1 point = 0.01 USD.
        # If trade_tick_size differs, scale appropriately.
        tick_size = info.trade_tick_size if info.trade_tick_size > 0 else 0.01
        deviation = max(1, int(max_slippage_per_oz / tick_size))

        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "symbol": symbol,
            "volume": float(volume_lots),
            "type": order_type,
            "price": float(price),
            "sl": float(sl),
            "deviation": deviation,
            "magic": int(magic),
            "comment": comment[:31],
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        if tp is not None:
            request["tp"] = float(tp)

        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            err = mt5.last_error() if result is None else (result.retcode, result.comment)
            raise RuntimeError(f"MT5 order_send failed: {err}")

        return OrderTicket(
            broker_id=int(result.order),
            side=side,
            volume_lots=float(result.volume),
            open_price=float(result.price),
            sl=float(sl),
            tp=tp,
            comment=comment,
            opened_at_utc=datetime.now(tz=timezone.utc),
        )

    def modify_sl(self, ticket: OrderTicket, new_sl: float) -> bool:
        positions = mt5.positions_get(ticket=ticket.broker_id)
        if not positions:
            return False
        pos = positions[0]
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket.broker_id,
            "symbol": pos.symbol,
            "sl": float(new_sl),
            "tp": float(pos.tp),
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            return False
        ticket.sl = new_sl
        return True

    def modify_tp(self, ticket: OrderTicket, new_tp: float) -> bool:
        positions = mt5.positions_get(ticket=ticket.broker_id)
        if not positions:
            return False
        pos = positions[0]
        request = {
            "action": mt5.TRADE_ACTION_SLTP,
            "position": ticket.broker_id,
            "symbol": pos.symbol,
            "sl": float(pos.sl),
            "tp": float(new_tp),
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            return False
        ticket.tp = new_tp
        return True

    def close(self, ticket: OrderTicket, volume_lots: float) -> float:
        positions = mt5.positions_get(ticket=ticket.broker_id)
        if not positions:
            raise RuntimeError(f"No open position with ticket {ticket.broker_id}")
        pos = positions[0]
        close_type = mt5.ORDER_TYPE_SELL if pos.type == mt5.POSITION_TYPE_BUY else mt5.ORDER_TYPE_BUY
        tick = mt5.symbol_info_tick(pos.symbol)
        price = tick.bid if close_type == mt5.ORDER_TYPE_SELL else tick.ask
        request = {
            "action": mt5.TRADE_ACTION_DEAL,
            "position": ticket.broker_id,
            "symbol": pos.symbol,
            "volume": float(volume_lots),
            "type": close_type,
            "price": float(price),
            "deviation": 5,
            "magic": int(pos.magic),
            "comment": "v3.0 close",
            "type_time": mt5.ORDER_TIME_GTC,
            "type_filling": mt5.ORDER_FILLING_IOC,
        }
        result = mt5.order_send(request)
        if result is None or result.retcode != mt5.TRADE_RETCODE_DONE:
            err = mt5.last_error() if result is None else (result.retcode, result.comment)
            raise RuntimeError(f"MT5 close failed: {err}")
        return float(result.price)

    def open_tickets(self, symbol: str, magic: int) -> List[OrderTicket]:
        positions = mt5.positions_get(symbol=symbol) or []
        tickets: List[OrderTicket] = []
        for p in positions:
            if p.magic != magic:
                continue
            tickets.append(OrderTicket(
                broker_id=int(p.ticket),
                side=OrderSide.BUY if p.type == mt5.POSITION_TYPE_BUY else OrderSide.SELL,
                volume_lots=float(p.volume),
                open_price=float(p.price_open),
                sl=float(p.sl),
                tp=float(p.tp) if p.tp != 0 else None,
                comment=p.comment,
                opened_at_utc=datetime.fromtimestamp(p.time, tz=timezone.utc),
            ))
        return tickets

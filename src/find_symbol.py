"""Find your broker's exact gold symbol name.

Your MT5 broker may not call spot gold "XAUUSD" - common variants include
XAUUSDm, XAUUSD.a, XAUUSD.pro, GOLD, GOLD.spot, etc. This helper lists every
symbol whose name or description matches a search term so you can copy the
exact string into SYMBOL= in your .env.

Usage (with MT5 running and logged in):
    python -m src.find_symbol           # defaults to searching "xau" + "gold"
    python -m src.find_symbol gold
    python -m src.find_symbol xau
"""
from __future__ import annotations

import sys

try:
    import MetaTrader5 as mt5
except ImportError:
    print("MetaTrader5 package not installed. Run: pip install MetaTrader5")
    sys.exit(1)


def main() -> int:
    terms = [t.lower() for t in sys.argv[1:]] or ["xau", "gold"]

    if not mt5.initialize():
        print(f"MT5 initialize failed: {mt5.last_error()}")
        print("Make sure the MetaTrader 5 terminal is running and logged in.")
        return 1

    try:
        symbols = mt5.symbols_get() or []
        matches = []
        for s in symbols:
            name = s.name
            desc = (getattr(s, "description", "") or "")
            if any(t in name.lower() or t in desc.lower() for t in terms):
                matches.append((name, desc))

        if not matches:
            print(f"No symbols matched {terms}. Try a broader term, e.g. "
                  "`python -m src.find_symbol gold`.")
            return 0

        print(f"Symbols matching {terms}:\n")
        print(f"  {'SYMBOL':<20} DESCRIPTION")
        print(f"  {'-' * 20} {'-' * 40}")
        for name, desc in sorted(matches):
            # Ensure visible so we can read a tick
            mt5.symbol_select(name, True)
            tick = mt5.symbol_info_tick(name)
            bid = f"{tick.bid:.2f}" if tick else "n/a"
            print(f"  {name:<20} {desc[:40]:<40} bid={bid}")
        print("\nCopy the exact spot-gold symbol into SYMBOL= in your .env,")
        print("then set MODE=dryrun (or live) to use MT5 prices.")
        return 0
    finally:
        mt5.shutdown()


if __name__ == "__main__":
    sys.exit(main())

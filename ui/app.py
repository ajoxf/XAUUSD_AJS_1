"""XAUUSD Fibonacci Range Bot v3.2 — Streamlit UI.

Launch with:
    streamlit run ui/app.py
"""
from __future__ import annotations

import sys
from pathlib import Path

# Ensure repo root is on sys.path so `from ui.X import Y` works when
# Streamlit launches this script directly.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import streamlit as st

from ui.components import get_settings, sidebar_controls

st.set_page_config(
    page_title="XAUUSD Fib Bot v3.2",
    page_icon="🟡",
    layout="wide",
    initial_sidebar_state="expanded",
)

settings = get_settings()
sidebar_controls()

st.title("XAUUSD Fibonacci Range Strategy — v3.2")
st.markdown(
    """
    This bot trades **Gold spot (XAU/USD)** using prior-day Fibonacci range
    projections. It runs intraday on the New York session, takes at most one
    trade per day, and exits everything before 21:00 UTC.

    **What it does in plain English:**
    1. At 00:05 UTC each day, it pre-computes today's trading levels from
       yesterday's price range.
    2. It waits for the New York session to open, then watches for the
       price to cross a specific level.
    3. It runs **six safety checks** (volatility, news events, trend
       direction, ...) and **four entry confirmations** before opening any
       trade.
    4. Once in a trade, it splits the position into three parts:
       40% closes at the first target, 30% at the second target,
       and 30% trails behind the price to capture extended moves.

    **Risk settings:** 3% of account per trade by default. Two automatic
    safety brakes lower this if win rate drops below safe thresholds. A
    circuit breaker halts everything if the account drops 30%.
    """
)

st.markdown("---")

st.subheader("Use the pages on the left")
cols = st.columns(4)
with cols[0]:
    st.markdown("### 📊 Dashboard")
    st.caption("Live view of today's setup, open position, "
                "and recent activity.")
with cols[1]:
    st.markdown("### ⚙️ Settings")
    st.caption("Configure broker credentials, risk, and which "
                "strategy filters are active.")
with cols[2]:
    st.markdown("### 🧪 Backtest")
    st.caption("Replay the strategy over the last weeks or months and "
                "see how it would have performed.")
with cols[3]:
    st.markdown("### 📜 Logs")
    st.caption("Detailed event log: every gate block, entry decision, "
                "and trade.")

st.markdown("---")

st.subheader("Quick start (for non-technical users)")
st.markdown(
    """
    1. **Settings** — Enter your MetaTrader 5 broker login. Choose your
       starting capital and risk %. Leave the feature toggles at their
       default values unless you know what you're doing.
    2. **Backtest** — Pick the last 30 days and click **Run backtest**.
       Make sure the equity curve goes up and the win rate is roughly 60%+.
    3. **Dashboard** — Press **▶ Start** in the left sidebar. Watch today's
       trade unfold in real time.
    4. To stop, press **■ Stop**. The bot will close any open position
       safely.
    """
)

st.info(
    "**Paper mode** (default) replays recent market history to demonstrate "
    "the strategy — nothing real is traded. Switch to **live mode** in "
    "Settings only after you've configured your broker and validated "
    "performance with a backtest.",
    icon="ℹ️",
)

"""Historical backtest using yfinance daily OHLC."""
from __future__ import annotations

from datetime import date, timedelta

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from ui.backtest import run_backtest
from ui.components import get_settings, sidebar_controls

st.set_page_config(page_title="Backtest — XAUUSD Bot", page_icon="🧪", layout="wide")
sidebar_controls()

st.title("🧪 Backtest")
st.caption("Replays the strategy over historical data and shows the equity curve, "
            "win rate, and individual trades.")

settings = get_settings()

with st.expander("ℹ️ About this backtest", expanded=False):
    st.markdown("""
This is a **daily-resolution approximation**. It applies every gate and
sizing rule that can be evaluated from daily OHLC, but it skips checks that
require intraday data (RSI and re-test confirmation). Outcomes are estimated
from each day's open/high/low/close path:

- **Bullish day** (close > open) → assumes path was open → high → low → close
- **Bearish day** → assumes path was open → low → high → close

This means actual live results will differ — typically the live bot fires
**fewer** trades (more rejections from intraday filters) but with **higher**
win rate. The backtest is a useful sanity check for "would this strategy
make money in this period?", not a guarantee.
""")

st.subheader("Configure")
c1, c2, c3 = st.columns(3)
with c1:
    end_default = date.today() - timedelta(days=1)
    end = st.date_input("End date", value=end_default, max_value=date.today())
with c2:
    period_choice = st.selectbox("Period",
                                  options=["Last 30 days", "Last 60 days",
                                            "Last 90 days", "Last 180 days",
                                            "Custom"])
    period_days = {"Last 30 days": 30, "Last 60 days": 60,
                    "Last 90 days": 90, "Last 180 days": 180,
                    "Custom": None}[period_choice]
with c3:
    if period_days is None:
        start = st.date_input("Start date", value=end - timedelta(days=60),
                                max_value=end)
    else:
        start = end - timedelta(days=period_days)
        st.markdown(f"**Start date:** {start}")

start_equity = st.number_input("Starting equity (USD)",
                                 value=float(settings.starting_equity),
                                 step=1000.0, min_value=1000.0)

run_clicked = st.button("▶ Run backtest", type="primary", use_container_width=True)

# ── Run ─────────────────────────────────────────────────
if run_clicked:
    from dataclasses import replace
    bt_settings = replace(settings, starting_equity=float(start_equity))

    progress_bar = st.progress(0.0, text="Loading data...")
    status_text = st.empty()

    def cb(pct: float, current: date):
        progress_bar.progress(min(pct, 1.0), text=f"Simulating {current}")

    try:
        with st.spinner("Running backtest..."):
            result = run_backtest(start, end, bt_settings, progress_cb=cb)
        st.session_state["bt_result"] = result
        progress_bar.empty()
    except Exception as exc:
        progress_bar.empty()
        st.error(f"Backtest failed: {exc}")
        st.session_state.pop("bt_result", None)

# ── Render result ───────────────────────────────────────
result = st.session_state.get("bt_result")
if result is not None:
    st.markdown("---")
    st.subheader("Results")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Trades", result.n_trades)
    c2.metric("TP1 win rate", f"{result.tp1_win_rate:.1f}%")
    c3.metric("Final equity", f"${result.final_equity:,.2f}",
                delta=f"{result.total_pnl:+,.2f}")
    c4.metric("Total return", f"{result.total_return_pct:.2f}%")
    c5.metric("Max drawdown", f"{result.max_drawdown_pct:.2f}%",
                delta_color="inverse")

    bcol = st.columns(3)
    bcol[0].metric("Blocked by gates", result.blocked_days)
    bcol[1].metric("No signal days", result.no_signal_days)
    bcol[2].metric("Skipped (no data)", result.skipped_days)

    # ── Equity curve ────────────────────────────────────
    if result.equity_curve:
        eq_df = pd.DataFrame(result.equity_curve, columns=["date", "equity"])
        peak_series = eq_df["equity"].cummax()
        dd_series = (eq_df["equity"] - peak_series) / peak_series * 100.0

        fig = go.Figure()
        fig.add_trace(go.Scatter(x=eq_df["date"], y=eq_df["equity"],
                                  mode="lines", name="Equity",
                                  line=dict(color="#10b981", width=2)))
        fig.add_trace(go.Scatter(x=eq_df["date"], y=peak_series,
                                  mode="lines", name="Peak",
                                  line=dict(color="#3b82f6", width=1, dash="dot"),
                                  opacity=0.4))
        fig.update_layout(title="Equity curve",
                           xaxis_title="Date", yaxis_title="Equity (USD)",
                           height=400, hovermode="x unified",
                           template="plotly_white")
        st.plotly_chart(fig, use_container_width=True)

        dd_fig = go.Figure()
        dd_fig.add_trace(go.Scatter(x=eq_df["date"], y=dd_series,
                                      mode="lines", fill="tozeroy",
                                      line=dict(color="#ef4444", width=1),
                                      name="Drawdown"))
        dd_fig.update_layout(title="Drawdown from peak (%)",
                              xaxis_title="Date", yaxis_title="Drawdown %",
                              height=250, template="plotly_white")
        st.plotly_chart(dd_fig, use_container_width=True)

    # ── Trade list ──────────────────────────────────────
    st.subheader("Individual trades")
    if not result.trades:
        st.info("No trades in this period.")
    else:
        tdf = pd.DataFrame([{
            "Date": t.trade_date,
            "Dir": t.direction,
            "Regime": t.regime,
            "Entry": round(t.entry_price, 2),
            "SL": round(t.sl, 2),
            "TP1": round(t.tp1, 2),
            "TP2": round(t.tp2, 2),
            "Lots": round(t.lots, 2),
            "Outcome": t.outcome,
            "Exit": round(t.exit_price, 2),
            "P&L": t.pnl,
            "Equity": t.equity_after,
        } for t in result.trades])

        # Colour outcome column for at-a-glance reading
        def colour_outcome(val):
            colour = {"TP1": "#10b981", "TP2": "#059669", "SL": "#ef4444",
                       "SESSION_END": "#6b7280", "NO_SIGNAL": "#9ca3af"}.get(val, "")
            return f"background-color: {colour}; color: white; font-weight: 600;" if colour else ""

        styled = tdf.style.map(colour_outcome, subset=["Outcome"]) \
                          .map(lambda v: "color: #10b981;" if isinstance(v, (int, float)) and v > 0
                               else ("color: #ef4444;" if isinstance(v, (int, float)) and v < 0 else ""),
                               subset=["P&L"])
        st.dataframe(styled, use_container_width=True, hide_index=True)

        # CSV download
        csv = tdf.to_csv(index=False).encode("utf-8")
        st.download_button("📥 Download trades as CSV", csv,
                            file_name=f"backtest_{result.equity_curve[0][0]}_{result.equity_curve[-1][0]}.csv",
                            mime="text/csv")
else:
    st.info("Configure the period above and click **Run backtest** to start.")

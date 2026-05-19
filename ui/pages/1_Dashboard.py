"""Live trading dashboard."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import pandas as pd
import streamlit as st

from ui.components import get_supervisor, sidebar_controls

try:
    from streamlit_autorefresh import st_autorefresh
    _AUTOREFRESH = True
except ImportError:
    _AUTOREFRESH = False

st.set_page_config(page_title="Dashboard — XAUUSD Bot", page_icon="📊", layout="wide")
sidebar_controls()

st.title("📊 Dashboard")
st.caption("Refreshes every 3 seconds while the bot is running.")

sup = get_supervisor()
if sup.is_running and _AUTOREFRESH:
    st_autorefresh(interval=3000, key="dashboard_refresh")

snap = sup.snapshot(recent_events_limit=30)

# ── Top-line metrics ─────────────────────────────────────
c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Equity", f"${snap.equity:,.2f}",
          delta=f"{snap.equity - snap.starting_equity:+,.2f}")
c2.metric("Peak", f"${snap.peak_equity:,.2f}")
c3.metric("Drawdown", f"{snap.drawdown_pct:.2f}%",
          delta=f"-{snap.drawdown_pct:.2f}%" if snap.drawdown_pct > 0 else "0.00%",
          delta_color="inverse")
c4.metric("Active risk", f"{snap.monitor.get('active_risk_pct', 0):.2f}%")
c5.metric("Status", snap.status.upper())

st.markdown("---")

# ── Today's premarket ────────────────────────────────────
st.subheader(f"Today's plan{' — ' + str(snap.today) if snap.today else ''}")
if snap.premarket is None:
    st.info("Pre-market context not yet built. Press **▶ Start** in the "
             "sidebar to begin.")
else:
    pm = snap.premarket
    if pm["event_blocked"]:
        st.error(f"🚫 Trading blocked today: {pm['event_reason']}")

    lcol, rcol = st.columns(2)
    with lcol:
        st.markdown("**Market context**")
        sma_status = "ABOVE ✅ long-term up-trend" if pm['prev_close'] > pm['sma200_daily'] \
            else "BELOW ⚠️ long-term down-trend (shorts allowed)"
        st.markdown(f"""
- **Yesterday's close:** `${pm['prev_close']:.2f}`
- **Yesterday's range:** `${pm['range']:.2f}`
- **20-day ATR:** `${pm['atr_20']:.2f}`
- **200-day SMA:** `${pm['sma200_daily']:.2f}` — price is {sma_status}
- **Volatility regime:** `{pm['regime']}` (ratio `{pm['regime_ratio']}`)
- **Trend bias (4H):** `{pm['trend_bias']}`
- **Seasonal multiplier:** `{pm['seasonal_mult_long']:.2f}` (longs only)
- **Yesterday's outcome:** `{pm.get('prev_session_close_type') or '—'}`
""")
        active_opts = []
        if pm["filter_c_active"]:
            active_opts.append("**C** — TP1 reduced to 50% (low-volatility day)")
        if pm["long_filter_f"] or pm["short_filter_f"]:
            side = 'long' if pm['long_filter_f'] else 'short'
            active_opts.append(f"**F** — TP2 extended to 127% on {side} side")
        if pm.get("back_to_back_active"):
            active_opts.append("**Opt 2** — back-to-back TP2 extension (+10%)")
        if pm.get("high_atr_extension_active"):
            active_opts.append("**Opt 3** — high-ATR TP2 extension (115%)")
        if active_opts:
            st.caption("Active enhancements: " + " · ".join(active_opts))

    with rcol:
        st.markdown("**Trade levels**")
        st.markdown(f"""
| | Long | Short |
|---|---|---|
| Entry | `${pm['long_entry']:.2f}` | `${pm['short_entry']:.2f}` |
| Stop loss | `${pm['long_sl']:.2f}` | `${pm['short_sl']:.2f}` |
| Take profit 1 | `${pm['long_tp1']:.2f}` | `${pm['short_tp1']:.2f}` |
| Take profit 2 ({pm.get('long_tp2_fib', 1.0):.2f}× / {pm.get('short_tp2_fib', 1.0):.2f}×) | `${pm['long_tp2']:.2f}` | `${pm['short_tp2']:.2f}` |
""")
        st.caption(f"Session: {pm['session_start_utc'].strftime('%H:%M')} → "
                    f"{pm['session_end_utc'].strftime('%H:%M')} UTC · "
                    "20:55 UTC partial close · 21:00 UTC hard stop")

st.markdown("---")

# ── Open position ────────────────────────────────────────
st.subheader("Open position")
if snap.position is None:
    st.info("No open position right now.")
else:
    p = snap.position
    pcols = st.columns(5)
    pcols[0].metric("Direction", p["direction"])
    pcols[1].metric("Entry", f"${p['entry_price']:.2f}")
    pcols[2].metric("Current stop", f"${p['current_stop']:.2f}")
    pcols[3].metric("TP1", f"${p['tp1']:.2f}",
                     delta="✓ hit" if p["tp1_hit"] else "pending")
    pcols[4].metric("TP2", f"${p['tp2']:.2f}",
                     delta="✓ hit" if p["tp2_hit"] else "pending")

    if p["accelerated_tp1"]:
        kind = p.get("accelerated_kind", "STANDARD")
        if kind == "WEDNESDAY":
            st.caption("⏰ Wednesday TP1 acceleration — moved to 70% of original at 14:30 NY")
        else:
            st.caption("⏰ TP1 accelerated to 80% of original distance at 15:30 NY")
    if p.get("rsi_trim_done"):
        st.caption("✂️ RSI post-TP1 trim check completed")

    st.markdown("**Halves (v3.2 — 50/50 split):**")
    tdf = pd.DataFrame(p["tranches"])
    if not tdf.empty:
        tdf["status"] = tdf.apply(
            lambda r: "🟢 OPEN" if r["open"]
            else f"🔴 CLOSED @ ${r['close_price']:.2f} ({r['close_reason']})",
            axis=1,
        )
        st.dataframe(tdf[["name", "lots", "status"]], use_container_width=True,
                     hide_index=True)
        # Surface any partial closes (RSI trim)
        for tr in p["tranches"]:
            for pc in tr.get("partial_closes", []) or []:
                st.caption(f"↳ {tr['name']} partial: {pc.get('trim_lots')} lots "
                            f"({pc.get('reason')}) @ RSI {pc.get('rsi_at_trim')}")

st.markdown("---")

# ── Week-to-date ─────────────────────────────────────────
st.subheader("Week so far")
wcol = st.columns(6)
w = snap.week
wcol[0].metric("Trades", w.get("trades", 0))
wcol[1].metric("TP1 wins", w.get("wins_tp1", 0))
wcol[2].metric("TP2 wins", w.get("wins_tp2", 0))
wcol[3].metric("Stop-outs", w.get("sls", 0))
wcol[4].metric("Regime exits", w.get("regime_exits", 0))
wcol[5].metric("TP1 accelerations", w.get("tp1_accelerations", 0))

st.markdown("**Gates that blocked trades this week:**")
gcol = st.columns(6)
gcol[0].metric("News events", w.get("gate_block_event", 0))
gcol[1].metric("Too quiet (ATR floor)", w.get("gate_block_atr_floor", 0))
gcol[2].metric("Too volatile (ATR cap)", w.get("gate_block_atr_cap", 0))
gcol[3].metric("Wrong trend (4H)", w.get("gate_block_trend", 0))
gcol[4].metric("SMA200 short filter", w.get("gate_block_sma200", 0))
gcol[5].metric("Already traded", w.get("gate_block_daily_lock", 0))

st.markdown("**v3.2 enhancement activity:**")
vcol = st.columns(6)
vcol[0].metric("Wednesday accels", w.get("wednesday_accelerations", 0))
vcol[1].metric("COMEX vol exits", w.get("comex_vol_exits", 0))
vcol[2].metric("RSI trims", w.get("rsi_trims", 0))
vcol[3].metric("Back-to-back TP2", w.get("back_to_back_tp2", 0))
vcol[4].metric("High-ATR TP2", w.get("high_atr_tp2", 0))
vcol[5].metric("20:55 half2 close", w.get("session_close_half2", 0))

# ── Monitors ─────────────────────────────────────────────
st.markdown("---")
st.subheader("Safety monitors")
mcol = st.columns(4)
m = snap.monitor
mcol[0].metric("Fast win rate (20 trades)",
                f"{m.get('win_rate_fast_20')}%" if m.get("win_rate_fast_20") is not None
                else "n/a")
mcol[1].metric("Slow win rate (50 trades)",
                f"{m.get('win_rate_slow_50')}%" if m.get("win_rate_slow_50") is not None
                else "n/a")
mcol[2].metric("Consecutive losses", m.get("consecutive_losses", 0))
mcol[3].metric("Brakes active",
                "Slow" if m.get("slow_active") else ("Fast" if m.get("fast_active") else "None"))

# ── Recent activity ──────────────────────────────────────
st.markdown("---")
st.subheader("Recent activity")
if not snap.recent_events:
    st.caption("No events yet.")
else:
    rows = []
    for e in snap.recent_events[-15:]:
        rows.append({
            "Time (UTC)": e.get("ts", "")[:19].replace("T", " "),
            "Event": e.get("kind", ""),
            "Detail": ", ".join(f"{k}={v}" for k, v in e.items()
                                if k not in ("ts", "kind"))[:120],
        })
    st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

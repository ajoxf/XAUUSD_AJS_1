"""Settings: broker credentials, risk, mode, feature flags."""
from __future__ import annotations

import sys
from dataclasses import asdict, replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent.parent))

import streamlit as st

from config.flags import FLAGS
from config.settings import Settings
from ui.components import (get_settings, get_supervisor, replace_supervisor,
                             sidebar_controls, save_env_file)

st.set_page_config(page_title="Settings — XAUUSD Bot", page_icon="⚙️", layout="wide")
sidebar_controls()

st.title("⚙️ Settings")
st.caption("Changes are saved to `.env` and applied next time the bot starts.")

settings = get_settings()
sup = get_supervisor()

if sup.is_running:
    st.warning("The bot is currently running. Stop it before saving changes — "
                "settings won't apply until the next start.")

with st.form("settings_form"):
    st.subheader("Operational mode")
    mode_descriptions = {
        "paper": "Replays recent market history through the bot. No real trades — "
                  "good for learning and demos.",
        "live": "Routes orders to MetaTrader 5. Requires a running MT5 terminal "
                 "and credentials below.",
        "dryrun": "Computes everything but never sends an order. Useful for "
                   "verifying logic against the live market without risk.",
    }
    mode = st.radio("Trading mode",
                     options=["paper", "live", "dryrun"],
                     index=["paper", "live", "dryrun"].index(settings.mode),
                     horizontal=True,
                     captions=[mode_descriptions["paper"],
                               mode_descriptions["live"],
                               mode_descriptions["dryrun"]])

    st.markdown("---")
    st.subheader("Trading parameters")
    c1, c2 = st.columns(2)
    with c1:
        symbol = st.text_input("Symbol", value=settings.symbol,
                                help="MT5 symbol code for spot gold. Some brokers "
                                      "use XAUUSD, XAUUSDm, GOLD, or XAUUSD.pro.")
        starting_equity = st.number_input("Starting equity (USD)",
                                            value=float(settings.starting_equity),
                                            step=1000.0, min_value=1000.0,
                                            help="Used as the reference for risk %, "
                                                  "circuit breaker, and reporting.")
    with c2:
        risk_pct = st.slider("Risk per trade (%)",
                              min_value=0.5, max_value=5.0,
                              value=float(settings.risk_pct * 100),
                              step=0.25,
                              help="Default 3.0% per v3.2 spec. Auto-reduced to 2.0% "
                                    "or 1.5% by the safety monitors if win rate drops.")
        magic = st.number_input("Magic number",
                                  value=int(settings.magic_number), step=1,
                                  help="Identifier MT5 uses to recognise this bot's "
                                        "orders. Leave unchanged unless you run "
                                        "multiple bots.")

    st.markdown("---")
    st.subheader("MetaTrader 5 credentials")
    st.caption("Required for **live** mode only. Stored locally in `.env`, "
                "never sent anywhere.")
    c3, c4 = st.columns(2)
    with c3:
        mt5_login = st.text_input("Login", value=str(settings.mt5_login) or "")
        mt5_password = st.text_input("Password", value=settings.mt5_password,
                                       type="password")
    with c4:
        mt5_server = st.text_input("Server", value=settings.mt5_server,
                                     help="e.g. ICMarketsSC-Demo")
        mt5_terminal_path = st.text_input("Terminal path (optional)",
                                            value=settings.mt5_terminal_path,
                                            help="Full path to terminal64.exe. "
                                                  "Leave blank to auto-detect.")

    st.markdown("---")
    st.subheader("Logging")
    log_level = st.selectbox("Log level",
                              options=["DEBUG", "INFO", "WARNING", "ERROR"],
                              index=["DEBUG", "INFO", "WARNING", "ERROR"].index(
                                  settings.log_level))
    log_dir = st.text_input("Log directory", value=str(settings.log_dir))

    submit = st.form_submit_button("💾 Save settings")

if submit:
    new_settings = replace(
        settings,
        symbol=symbol,
        starting_equity=float(starting_equity),
        risk_pct=float(risk_pct) / 100.0,
        magic_number=int(magic),
        mode=mode,
        mt5_login=int(mt5_login) if mt5_login.isdigit() else 0,
        mt5_password=mt5_password,
        mt5_server=mt5_server,
        mt5_terminal_path=mt5_terminal_path,
        log_level=log_level,
        log_dir=Path(log_dir),
    )
    save_env_file(Path(".env"), {
        "SYMBOL": new_settings.symbol,
        "STARTING_EQUITY": new_settings.starting_equity,
        "RISK_PCT": new_settings.risk_pct,
        "MAGIC_NUMBER": new_settings.magic_number,
        "MODE": new_settings.mode,
        "MT5_LOGIN": new_settings.mt5_login,
        "MT5_PASSWORD": new_settings.mt5_password,
        "MT5_SERVER": new_settings.mt5_server,
        "MT5_TERMINAL_PATH": new_settings.mt5_terminal_path,
        "LOG_LEVEL": new_settings.log_level,
        "LOG_DIR": str(new_settings.log_dir),
    })
    replace_supervisor(new_settings)
    st.success("Settings saved to `.env`. Press **▶ Start** in the sidebar.")

st.markdown("---")
st.subheader("Strategy filters & optimisations")
st.caption("All toggles default ON (v3.2 production config). "
            "Switch to OFF only for A/B testing. Changes take effect "
            "immediately for the next signal evaluation.")

flag_groups = {
    "Original filters (v1.0)": [
        ("FILTER_A_ATR_RANGE", "Skip days where range < 20-day ATR (too quiet)"),
        ("FILTER_A_ATR_CAP", "Skip days where range > 1.8× ATR (post-shock)"),
        ("FILTER_B_SESSION_DST", "Trade only inside DST-aware NY session window"),
        ("FILTER_C_DYNAMIC_TP1", "Reduce TP1 to 50% on low-volatility days"),
        ("FILTER_D_TRAILING_STOP", "Trail the final tranche after TP2"),
        ("FILTER_E_EVENT_AVOID", "Skip FOMC/NFP/CPI days"),
        ("FILTER_F_EXTENDED_TP2", "Extend TP2 to 127% on trend-confirming days"),
        ("FILTER_CF_MUTUAL_EXCLUSION", "Don't combine extended TP2 with reduced TP1"),
    ],
    "Loophole fixes (v2.0)": [
        ("FIX_L1_CANDLE_CONFIRM", "Require a closed candle (not just a wick) to confirm"),
        ("FIX_L2_DAILY_LOCK", "Hard one-trade-per-day lock (no second attempts)"),
        ("FIX_L3_ATR_CAP", "Also cap range against 10-day median (extra stability check)"),
        ("FIX_L4_DST_AWARE_SESSION", "Auto-adjust session window for DST changes"),
        ("FIX_L5_CF_MUTUAL_EXCLUSION", "Mutual exclusion logic for filter combinations"),
        ("FIX_L6_DYNAMIC_TRAIL", "Use today's intraday range to size the trailing stop"),
        ("FIX_L7_PRE_EVENT_BLOCK", "Also block the day before FOMC and NFP"),
        ("FIX_L8_ROUNDING_AUDIT", "Warn if lot rounding shifts actual risk by >5%"),
        ("FIX_L9_FAST_WINRATE_MONITOR", "20-trade fast brake on win rate drops"),
    ],
    "v3.0 / 3.1 optimisations": [
        ("OPT_STEP1_BODY_60PCT", "Require confirmation candle body ≥ 60% of range"),
        ("OPT_STEP2_4H_TREND_HARD", "Hard block trades against the 4-hour trend"),
        ("OPT_STEP3_RSI_ZONE", "Only enter when RSI is in the neutral momentum zone"),
        ("OPT_STEP4_RETEST_CONFIRM", "Require re-test or clean continuation after breakout"),
        ("OPT_50_50_EXIT", "Use 50/50 exit (v3.1) — half at TP1, half at TP2"),
        ("OPT_SESSION_FORCE_CLOSE_2055", "Partial-close at 20:55 UTC (no overnight risk)"),
        ("OPT_BREAKEVEN_PLUS", "Move stop to entry + 30% of TP1 gain after TP1 hit"),
        ("OPT_TP1_TIME_ACCEL", "Move TP1 closer at 15:30 NY if not yet hit"),
        ("OPT_SEASONAL_SIZING", "Adjust long position size by month (gold seasonality)"),
        ("OPT_REGIME_DETECTOR", "Switch parameters by volatility regime"),
        ("OPT_3PCT_RISK", "Use 3% base risk (vs 2% in v2.0)"),
    ],
    "v3.2 return enhancements": [
        ("OPT_1_COMEX_VOL_CONTINUATION",
         "Exit Half 2 early when COMEX volume fades (2 bars < 50% of avg)"),
        ("OPT_2_BACK_TO_BACK_TP2",
         "Push TP2 further out when yesterday closed at TP2"),
        ("OPT_3_HIGH_ATR_TP2_EXTENSION",
         "Push TP2 to 115% on high-energy days (range 1.3–1.8× ATR)"),
        ("OPT_4_SMA200_SHORT_FILTER",
         "Block short trades when price is above the 200-day SMA"),
        ("OPT_5_WEDNESDAY_ACCEL",
         "On Wednesdays, accelerate TP1 earlier (14:30 NY, tighter 70% target)"),
        ("OPT_6_RSI_POST_TP1_TRIM",
         "Trim 25% of Half 2 if RSI is overextended right after TP1"),
    ],
}

tabs = st.tabs(list(flag_groups.keys()))
for tab, (group_name, items) in zip(tabs, flag_groups.items()):
    with tab:
        for key, label in items:
            current = getattr(FLAGS, key)
            new_val = st.checkbox(label, value=current, key=f"flag_{key}")
            if new_val != current:
                setattr(FLAGS, key, new_val)

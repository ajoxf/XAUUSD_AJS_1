"""HTML page routes."""
from __future__ import annotations

from flask import Blueprint, current_app, render_template

from config.flags import FLAGS, FeatureFlags

bp = Blueprint("views", __name__)


@bp.get("/")
def dashboard():
    return render_template("dashboard.html", page="dashboard")


@bp.get("/settings")
def settings_page():
    s = current_app.config["SETTINGS"]
    flag_meta = _flag_descriptions()
    flags = {k: getattr(FLAGS, k) for k in vars(FeatureFlags()).keys()}
    return render_template("settings.html", page="settings",
                            settings=s, flags=flags, flag_meta=flag_meta)


@bp.get("/backtest")
def backtest_page():
    return render_template("backtest.html", page="backtest")


@bp.get("/logs")
def logs_page():
    return render_template("logs.html", page="logs")


def _flag_descriptions():
    """Plain-English description for each feature flag, grouped."""
    return {
        "Original filters (v1.0)": [
            ("FILTER_A_ATR_RANGE", "Skip days where range < 20-day ATR (too quiet)"),
            ("FILTER_A_ATR_CAP", "Skip days where range > 1.8x ATR (post-shock)"),
            ("FILTER_B_SESSION_DST", "Trade only inside DST-aware NY session window"),
            ("FILTER_C_DYNAMIC_TP1", "Reduce TP1 to 50% on low-volatility days"),
            ("FILTER_D_TRAILING_STOP", "Trail the final tranche after TP2"),
            ("FILTER_E_EVENT_AVOID", "Skip FOMC/NFP/CPI days"),
            ("FILTER_F_EXTENDED_TP2", "Extend TP2 to 127% on trend-confirming days"),
            ("FILTER_CF_MUTUAL_EXCLUSION", "Don't combine extended TP2 with reduced TP1"),
        ],
        "Loophole fixes (v2.0)": [
            ("FIX_L1_CANDLE_CONFIRM", "Require a closed candle (not just a wick) to confirm"),
            ("FIX_L2_DAILY_LOCK", "Hard one-trade-per-day lock"),
            ("FIX_L3_ATR_CAP", "Also cap range against 10-day median"),
            ("FIX_L4_DST_AWARE_SESSION", "Auto-adjust session window for DST changes"),
            ("FIX_L5_CF_MUTUAL_EXCLUSION", "Mutual exclusion logic for filter combinations"),
            ("FIX_L6_DYNAMIC_TRAIL", "Use today's intraday range to size the trailing stop"),
            ("FIX_L7_PRE_EVENT_BLOCK", "Also block the day before FOMC and NFP"),
            ("FIX_L8_ROUNDING_AUDIT", "Warn if lot rounding shifts actual risk by >5%"),
            ("FIX_L9_FAST_WINRATE_MONITOR", "20-trade fast brake on win rate drops"),
        ],
        "v3.0 / 3.1 optimisations": [
            ("OPT_STEP1_BODY_60PCT", "Confirmation candle body >= 60% of range"),
            ("OPT_STEP2_4H_TREND_HARD", "Hard block trades against the 4-hour trend"),
            ("OPT_STEP3_RSI_ZONE", "RSI in neutral momentum zone at entry"),
            ("OPT_STEP4_RETEST_CONFIRM", "Require re-test or clean continuation"),
            ("OPT_50_50_EXIT", "50/50 exit (half at TP1, half at TP2)"),
            ("OPT_SESSION_FORCE_CLOSE_2055", "Partial-close at 20:55 UTC (no overnight)"),
            ("OPT_BREAKEVEN_PLUS", "Stop to entry + 30% of TP1 gain after TP1"),
            ("OPT_TP1_TIME_ACCEL", "Move TP1 closer at 15:30 NY if not yet hit"),
            ("OPT_SEASONAL_SIZING", "Adjust long size by month (gold seasonality)"),
            ("OPT_REGIME_DETECTOR", "Switch parameters by volatility regime"),
            ("OPT_3PCT_RISK", "Use 3% base risk"),
        ],
        "v3.2 return enhancements": [
            ("OPT_1_COMEX_VOL_CONTINUATION",
             "Exit Half 2 on COMEX volume fade (2 bars < 50% avg)"),
            ("OPT_2_BACK_TO_BACK_TP2", "Push TP2 further after yesterday closed at TP2"),
            ("OPT_3_HIGH_ATR_TP2_EXTENSION", "TP2 to 115% on high-energy ATR days"),
            ("OPT_4_SMA200_SHORT_FILTER", "Block shorts when price > 200-day SMA"),
            ("OPT_5_WEDNESDAY_ACCEL", "Wednesday TP1 to 70% at 14:30 NY"),
            ("OPT_6_RSI_POST_TP1_TRIM", "Trim 25% of Half 2 on RSI extreme post-TP1"),
        ],
    }

"""Feature flags from spec §12. All default True. Toggle for A/B testing."""
from dataclasses import dataclass


@dataclass
class FeatureFlags:
    # ── Original optimisation filters ────────────────────
    FILTER_A_ATR_RANGE: bool = True
    FILTER_A_ATR_CAP: bool = True
    FILTER_B_SESSION_DST: bool = True
    FILTER_C_DYNAMIC_TP1: bool = True
    FILTER_D_TRAILING_STOP: bool = True
    FILTER_E_EVENT_AVOID: bool = True
    FILTER_F_EXTENDED_TP2: bool = True
    FILTER_CF_MUTUAL_EXCLUSION: bool = True

    # ── v2.0 loophole fixes ──────────────────────────────
    FIX_L1_CANDLE_CONFIRM: bool = True
    FIX_L2_DAILY_LOCK: bool = True
    FIX_L3_ATR_CAP: bool = True
    FIX_L4_DST_AWARE_SESSION: bool = True
    FIX_L5_CF_MUTUAL_EXCLUSION: bool = True
    FIX_L6_DYNAMIC_TRAIL: bool = True
    FIX_L7_PRE_EVENT_BLOCK: bool = True
    FIX_L8_ROUNDING_AUDIT: bool = True
    FIX_L9_FAST_WINRATE_MONITOR: bool = True

    # ── v3.0 / 3.1 optimisations ─────────────────────────
    OPT_STEP1_BODY_60PCT: bool = True
    OPT_STEP2_4H_TREND_HARD: bool = True
    OPT_STEP3_RSI_ZONE: bool = True
    OPT_STEP4_RETEST_CONFIRM: bool = True
    OPT_50_50_EXIT: bool = True                 # v3.1: half at TP1, half at TP2
    OPT_SESSION_FORCE_CLOSE_2055: bool = True   # v3.1: 20:55 UTC partial close
    OPT_BREAKEVEN_PLUS: bool = True
    OPT_TP1_TIME_ACCEL: bool = True
    OPT_SEASONAL_SIZING: bool = True
    OPT_REGIME_DETECTOR: bool = True
    OPT_3PCT_RISK: bool = True
    OPT_DOM_GATE: bool = False                  # disabled — retail DOM unreliable

    # ── v3.2 return-enhancement optimisations ────────────
    OPT_1_COMEX_VOL_CONTINUATION: bool = True
    OPT_2_BACK_TO_BACK_TP2: bool = True
    OPT_3_HIGH_ATR_TP2_EXTENSION: bool = True
    OPT_4_SMA200_SHORT_FILTER: bool = True
    OPT_5_WEDNESDAY_ACCEL: bool = True
    OPT_6_RSI_POST_TP1_TRIM: bool = True


FLAGS = FeatureFlags()

"""Weekly performance report — spec §10."""
from __future__ import annotations

from datetime import date
from typing import Dict

from src.engine.state import EngineState


def weekly_report(state: EngineState, week_ending: date, equity: float,
                  equity_start_of_week: float) -> Dict:
    wins_tp1 = state.week_wins_tp1
    trades = max(state.week_trades, 1)
    wr_tp1 = wins_tp1 / trades * 100.0
    wr_tp2 = state.week_wins_tp2 / trades * 100.0
    sl_rate = state.week_sls / trades * 100.0

    mon = state.win_rate_monitor
    fast_rate = mon._rate(20)
    slow_rate = mon._rate(50)

    return {
        "week_ending": week_ending.isoformat(),
        "trades_executed": state.week_trades,
        "gate_blocks": {
            "event_or_pre_event": state.gate_block_event,
            "atr_floor": state.gate_block_atr_floor,
            "atr_cap": state.gate_block_atr_cap,
            "trend_alignment": state.gate_block_trend,
            "daily_lock": state.gate_block_daily_lock,
            "no_signal": state.gate_block_no_signal,
        },
        "entry_quality": {
            "layer_b_fails": state.week_b_fails,
            "layer_c_fails": state.week_c_fails,
            "layer_d_fails": state.week_d_fails,
        },
        "outcomes": {
            "win_rate_tp1_pct": round(wr_tp1, 2),
            "win_rate_tp2_pct": round(wr_tp2, 2),
            "sl_rate_pct": round(sl_rate, 2),
            "regime_exits": state.week_regime_exits,
            "tp1_accelerations": state.week_tp1_accels,
            "tranche_3_extended_wins": state.week_t3_extended,
        },
        "financials": {
            "net_pnl_this_week": round(equity - equity_start_of_week, 2),
            "equity_start_of_week": equity_start_of_week,
            "equity_end_of_week": equity,
            "running_peak": state.peak_equity,
            "current_drawdown_pct": round(state.current_drawdown_pct(equity), 2),
        },
        "monitors": {
            "win_rate_fast_20": round(fast_rate * 100, 2) if fast_rate is not None else None,
            "win_rate_slow_50": round(slow_rate * 100, 2) if slow_rate is not None else None,
            "fast_monitor_active": mon.fast_active,
            "slow_monitor_active": mon.slow_active,
            "consecutive_losses": state.loss_counter.count,
            "active_risk_pct": round(mon.evaluate() * 100, 3),
        },
    }


def reset_week(state: EngineState) -> None:
    state.week_trades = 0
    state.week_wins_tp1 = 0
    state.week_wins_tp2 = 0
    state.week_sls = 0
    state.week_regime_exits = 0
    state.week_tp1_accels = 0
    state.week_t3_extended = 0
    state.week_b_fails = 0
    state.week_c_fails = 0
    state.week_d_fails = 0
    state.gate_block_event = 0
    state.gate_block_atr_floor = 0
    state.gate_block_atr_cap = 0
    state.gate_block_trend = 0
    state.gate_block_daily_lock = 0
    state.gate_block_no_signal = 0

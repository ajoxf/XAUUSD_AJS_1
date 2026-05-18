# XAUUSD Fibonacci Range Strategy Bot — v3.0

Live trading bot for XAU/USD (Gold Spot) implementing the v3.0 master prompt:
prior-day Fibonacci range projections with six pre-trade gates, four-layer
entry confirmation, three-tranche exit management, regime/seasonal sizing
adjustments, and full safety controls.

## Architecture

```
config/        feature flags + runtime settings
ui/            Streamlit web app
  app.py         landing page
  supervisor.py  engine lifecycle (background thread)
  backtest.py    historical backtest engine
  components.py  shared widgets + session state
  pages/         Dashboard, Settings, Backtest, Logs
src/strategy/  pure rule engine (no I/O)
  indicators   ATR, EMA, RSI, median
  premarket    daily pre-market context (run once at 00:05 UTC)
  gates        six pre-trade gates
  entry        Layers A→D entry evaluation
  sizing       3% risk + seasonal/regime/alignment + audit
  exits        three-tranche manager + breakeven-plus + dynamic trail
  regime       ATR(20)/ATR(50) volatility regime
  seasonal     monthly long-bias multiplier
  events       FOMC/NFP/CPI calendar 2021–2026
  session      DST-aware NY session window
  safety       circuit breaker, win-rate monitors, daily lock, loss counter
src/data/      DataFeed protocol + yfinance implementation
src/broker/    BrokerAdapter protocol + MT5 + paper adapters
src/engine/    runtime: state, structured logger, runner, weekly reports
src/main.py    CLI entry point
tests/         unit + integration tests
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env with your MT5 broker credentials (or do this from the UI)
```

`MetaTrader5` requires the MT5 terminal running locally (Windows or Wine).
Other dependencies are cross-platform.

## Web UI (recommended for non-technical users)

```bash
streamlit run ui/app.py
```

Opens at `http://localhost:8501` with four pages:

- **📊 Dashboard** — live equity, today's Fibonacci levels, open position,
  tranche status, win-rate monitors, recent activity (auto-refreshes every
  3 s while the bot is running).
- **⚙️ Settings** — broker credentials, risk %, starting equity, mode
  (paper / live / dryrun), and all 28 strategy feature flags with plain-
  English descriptions. Saves to `.env`.
- **🧪 Backtest** — pick a date range, click Run, see the equity curve,
  drawdown chart, win rate, and per-trade table (downloadable as CSV).
- **📜 Logs** — filterable event log with download buttons.

The sidebar has **▶ Start** / **■ Stop** buttons and shows live status.
In paper mode (default), Start replays the last ~30 trading days through
the engine at accelerated speed — no broker required.

## CLI usage

```bash
# inspect today's pre-market context
python -m src.main premarket

# run paper-trading loop (no broker required)
python -m src.main paper

# run live (requires MT5 terminal + credentials in .env)
python -m src.main live
```

## Tests

```bash
pytest                      # unit + integration
pytest tests/unit -q        # unit only
pytest -m integration       # integration only
```

## Feature flags

All optimisation filters and v3.0 enhancements are gated by booleans in
`config/flags.py`. Each defaults to `True` (the v3.0 production config); flip
any of them to `False` for A/B comparison.

## Operational notes

- **Pre-market** runs once per day at 00:05 UTC. All Fibonacci levels, regime
  classification, trend bias, and event blocks are frozen for the session.
- **No second trade** ever fires the same UTC calendar day (Fix L2).
- **No overnight positions** — any open tranche is force-closed at 21:00 UTC.
- **Circuit breaker** halts trading if equity falls 30% below starting
  capital. Manual restart required.
- **Win-rate monitors** auto-reduce risk: 2% on fast (20-trade) breach,
  1.5% on sustained slow (50-trade) breach.
- **MT5 deviation/slippage**: `max_slippage_per_oz` in settings is converted
  to MT5 points using `symbol_info.trade_tick_size`.

## Live deployment checklist

1. Run 60+ trades on demo via `MODE=paper`.
2. Verify TP1 win rate is within 3pp of 64.6% target.
3. Verify no calendar month exceeds 8% drawdown in demo.
4. Tighten `circuit_breaker_pct` in `Settings` to 0.15 for go-live (overrides
   the default 0.30 — wider production threshold).
5. Switch `MODE=live` in `.env` and start under a process supervisor.
6. Widen circuit breaker back to 0.30 after 100 live trades.

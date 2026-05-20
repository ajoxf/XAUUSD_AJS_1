# XAUUSD Fibonacci Range Strategy Bot — v3.2

Live trading bot for XAU/USD (Gold Spot) implementing the v3.2 master prompt:
prior-day Fibonacci range projections with seven pre-trade gates, four-layer
entry confirmation, **50/50 exit** (Half 1 at TP1, Half 2 at TP2), 20:55 UTC
partial-close decision tree, regime/seasonal sizing, and **six v3.2 return
enhancements**:

1. **COMEX volume continuation** — exit Half 2 on momentum fade
2. **Back-to-back TP2 extension** — push TP2 further after consecutive wins
3. **High-ATR TP2 extension** — 115% TP2 on high-energy days
4. **SMA200 short filter** — block shorts above 200-day SMA
5. **Wednesday TP1 acceleration** — tighter, earlier TP1 on Wednesdays
6. **RSI post-TP1 trim** — close 25% of Half 2 when RSI extreme right after TP1

## Architecture

```
config/             feature flags + runtime settings (env-loaded)
webapp/             Flask production web app
  __init__.py         app factory
  server.py           Waitress launcher (entry point)
  supervisor.py       engine lifecycle (background thread)
  backtest.py         historical backtest engine
  auth.py             HTTP Basic Auth decorator
  api.py              JSON REST + SSE stream
  views.py            HTML page routes
  templates/          Jinja templates (Bootstrap 5)
  static/             CSS + ES6 JS modules
src/strategy/       pure rule engine (no I/O)
  indicators          ATR, EMA, RSI, SMA, median
  premarket           daily pre-market context (run once at 00:05 UTC)
  gates               seven pre-trade gates (G6 SMA200 short filter is v3.2)
  entry               Layers A→D entry evaluation
  sizing              3% risk + 50/50 split + seasonal/regime/alignment + audit
  exits               50/50 exit, breakeven-plus, dynamic trail, COMEX vol fade,
                        RSI post-TP1 trim, Wednesday acceleration, 20:55 UTC tree
  comex_volume        COMEX GC1! 15-min volume tracker (Opt 1)
  regime              ATR(20)/ATR(50) volatility regime
  seasonal            monthly long-bias multiplier
  events              FOMC/NFP/CPI calendar 2021–2026
  session             DST-aware NY session window
  safety              circuit breaker, win-rate monitors, daily lock, loss counter
src/data/           DataFeed protocol + MT5 feed (live) + yfinance (paper)
src/broker/         BrokerAdapter protocol + MT5 + paper adapters
src/integrations/   TradingView webhook receiver (port 5050)
src/engine/         runtime: state, structured logger, runner, weekly reports
src/main.py         CLI entry point (headless premarket/paper/live)
tests/              unit + integration tests
```

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env
# edit .env: set ADMIN_PASSWORD, MT5 credentials (live mode only)
```

`MetaTrader5` requires the MT5 terminal running locally (Windows or Wine).
Other dependencies are cross-platform.

## Web UI

```bash
python -m webapp.server
```

Opens at `http://localhost:8080` (configurable via `WEBAPP_HOST` /
`WEBAPP_PORT`). Backed by Waitress WSGI — production-grade, runs natively
on Windows.

**No authentication.** The webapp serves directly to whatever address
`WEBAPP_HOST` binds to. The default `127.0.0.1` keeps it on your local
machine. If you change `WEBAPP_HOST` to anything else (LAN IP, `0.0.0.0`,
public interface), put a reverse proxy in front (Caddy, nginx, Cloudflare
Tunnel) and gate access at that layer. The bot logs a warning at startup
if you bind beyond localhost.

Pages:

- **📊 Dashboard** — live equity, today's Fibonacci levels, open position,
  Half 1 / Half 2 status, weekly stats, v3.2 activity metrics, safety
  monitors, recent events. **Live-pushed** via Server-Sent Events — no
  polling, no auto-rerun overhead.
- **⚙️ Settings** — broker credentials, risk %, starting equity, mode
  (paper / live / dryrun), all strategy feature flags grouped by version
  with plain-English descriptions. Saves to `.env` over HTTP.
- **🧪 Backtest** — date-range backtest with Plotly equity + drawdown
  charts, per-trade table, CSV download.
- **📜 Logs** — filterable JSONL event viewer with download.

Sidebar shows **▶ Start / ■ Stop** controls, live status badge, and a
red error banner if the engine throws. Paper mode (default) replays the
last ~30 trading days through the live engine code path for demos.

### REST API

All endpoints under `/api`, JSON in/out, HTTP Basic Auth:

```
GET  /api/status            full snapshot (premarket, position, week, monitors)
GET  /api/status/lite       minimal payload for cheap polling
GET  /api/stream            Server-Sent Events: snapshot pushed every ~2s
POST /api/control/start     start the engine
POST /api/control/stop      stop the engine
GET  /api/settings          current Settings
POST /api/settings          update + persist to .env
GET  /api/flags             all feature flag values
POST /api/flags             {KEY: bool, ...} — toggle flags at runtime
POST /api/backtest          {start, end, starting_equity} → equity curve + trades
GET  /api/logs              ?kind=trade_open&limit=200
GET  /api/health            unauthenticated health check
```

## CLI usage (headless, no web UI)

```bash
python -m src.main premarket   # inspect today's pre-market context
python -m src.main paper        # paper-trading loop
python -m src.main live         # live (MT5 required)
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

## COMEX volume feed (Opt 1 — optional)

Opt 1 (COMEX volume continuation) needs a live feed of GC1! 15-min bar
volume. Use TradingView Pro+ alerts as the source:

1. Open a `GC1!` 15-minute chart on TradingView.
2. Add an alert: condition **"Every bar close"**.
3. Webhook URL: `http://YOUR_VPS_IP:5050/comex_volume`
4. Message body (paste exactly):
   ```json
   {"type":"volume_update","symbol":"GC1!","timeframe":"15",
    "volume":"{{volume}}","time":"{{timenow}}"}
   ```

Start the receiver alongside the bot — it runs in-process as a daemon
thread on port 5050. If no feed is configured (or it goes stale for >20
minutes), the bot falls back to the standard trail-to-TP2 exit without
blocking trading.

The receiver is implemented in Python stdlib only — no Flask required.

## Where prices come from

| Data | live / dryrun | paper |
|---|---|---|
| Daily / 4H / 15m OHLC (Fibonacci levels, ATR, SMA, EMA) | **MT5** (`copy_rates_from_pos`) | yfinance `GC=F` |
| Live ticker bid/ask/spread | MT5 `symbol_info_tick` | simulated |
| Order execution | MT5 | simulated |
| Balance / equity | MT5 `account_info` | `STARTING_EQUITY` |

In **live and dryrun** modes every number — levels, ticker, fills,
balance — comes from the same MT5 account/instrument, so the Fibonacci
levels line up with the prices your broker quotes.

In **paper** mode the levels come from yfinance's COMEX gold futures
(`GC=F`), which differ from spot XAUUSD by the futures basis. Paper mode
is for learning the mechanics; absolute price levels won't match a live
broker. Validate real levels in **dryrun** mode against your MT5 feed.

## Operational notes

- **Pre-market** runs once per day at 00:05 UTC. All Fibonacci levels, regime
  classification, trend bias, and event blocks are frozen for the session.
- **No second trade** ever fires the same UTC calendar day (Fix L2).
- **No overnight positions** — 20:55 UTC partial-close decision tree
  (v3.1) executes first, with 21:00 UTC hard close as final safety net.
- **Circuit breaker** halts trading if equity falls 30% below starting
  capital. Manual restart required.
- **Win-rate monitors** auto-reduce risk: 2% on fast (20-trade) breach,
  1.5% on sustained slow (50-trade) breach.
- **MT5 deviation/slippage**: `max_slippage_per_oz` in settings is converted
  to MT5 points using `symbol_info.trade_tick_size`.

## MT5 connection: attach vs credential mode

The MT5 adapter has two ways to connect:

**Attach mode (recommended for personal desktop use):**

Leave `MT5_LOGIN` blank in `.env`. Start MetaTrader 5 manually, log in to
your broker, and leave it running. The bot attaches to that running
terminal — it never sees your broker password.

```env
MT5_LOGIN=
MT5_PASSWORD=
MT5_SERVER=
```

**Credential mode (for headless / unattended deployment):**

Set `MT5_LOGIN`, `MT5_PASSWORD`, and `MT5_SERVER`. The bot logs MT5 in
itself on each start. The password is stored in plaintext in `.env`
(gitignored, but still on disk — set file permissions appropriately).

```env
MT5_LOGIN=12345678
MT5_PASSWORD=your-password
MT5_SERVER=ICMarketsSC-Demo
```

The bot picks attach vs credential mode automatically based on whether
`MT5_LOGIN` is set.

## Live deployment checklist

1. Run 60+ trades on demo via `MODE=paper`.
2. Verify TP1 win rate is within 3pp of 64.6% target.
3. Verify no calendar month exceeds 8% drawdown in demo.
4. Tighten the circuit breaker before going live: set
   `CIRCUIT_BREAKER_PCT=0.10` in `.env` for the first 30 trades. Step up
   gradually:
   - Trades 1–30: `0.10` (very tight; one bad sequence will halt the bot)
   - Trades 30–100: `0.15` (validating)
   - Trades 100+: `0.30` (production default)
5. Switch `MODE=live` in `.env` and start under a process supervisor
   (Task Scheduler on Windows, systemd on Linux).

/* Dashboard: render snapshots in real time + live ticker poller. */

(function () {
  const $ = id => document.getElementById(id);

  // ── Live ticker (300ms polling) ──────────────────────
  let lastBid = null, lastAsk = null;
  let tickerErrors = 0;

  function fmt(v) {
    if (v == null) return "—";
    return Number(v).toFixed(2);
  }

  function flash(el, direction) {
    el.classList.remove("tick-up", "tick-down");
    void el.offsetWidth;   // force reflow so the next class re-triggers transition
    el.classList.add(direction === "up" ? "tick-up" : "tick-down");
    setTimeout(() => el.classList.remove("tick-up", "tick-down"), 600);
  }

  function setSpreadClass(spread, max) {
    const el = $("ticker-spread");
    el.classList.remove("spread-ok", "spread-warn", "spread-block");
    if (spread == null || max == null) return;
    if (spread > max) el.classList.add("spread-block");
    else if (spread > max * 0.5) el.classList.add("spread-warn");
    else el.classList.add("spread-ok");
  }

  function ageString(isoTime) {
    if (!isoTime) return "—";
    const t = new Date(isoTime);
    const ms = Date.now() - t.getTime();
    if (ms < 0) return "just now";
    if (ms < 1500) return "just now";
    if (ms < 60_000) return `${Math.round(ms / 1000)}s ago`;
    if (ms < 3_600_000) return `${Math.round(ms / 60_000)}m ago`;
    return `${Math.round(ms / 3_600_000)}h ago`;
  }

  async function pollTicker() {
    try {
      const r = await fetch("/api/ticker");
      if (!r.ok) {
        tickerErrors++;
        if (tickerErrors > 3) setTickerState("error", "no data");
        return;
      }
      const t = await r.json();
      tickerErrors = 0;

      if (t.stale) {
        setTickerState("stale", t.error ? "stale" : "waiting");
        $("ticker-bid").textContent = "—";
        $("ticker-ask").textContent = "—";
        $("ticker-spread").textContent = "—";
        $("ticker-mid").textContent = "—";
        $("ticker-time").textContent = "—";
        $("ticker-age").textContent = "—";
        $("ticker-spread-limit").textContent = "";
        return;
      }

      setTickerState("live", "live");

      const bidEl = $("ticker-bid");
      const askEl = $("ticker-ask");
      bidEl.textContent = fmt(t.bid);
      askEl.textContent = fmt(t.ask);
      $("ticker-spread").textContent = "$" + fmt(t.spread);
      $("ticker-mid").textContent = fmt(t.mid);
      $("ticker-spread-limit").textContent =
        t.max_spread != null ? `limit $${fmt(t.max_spread)}` : "";

      if (lastBid != null && t.bid !== lastBid)
        flash(bidEl, t.bid > lastBid ? "up" : "down");
      if (lastAsk != null && t.ask !== lastAsk)
        flash(askEl, t.ask > lastAsk ? "up" : "down");
      lastBid = t.bid; lastAsk = t.ask;

      setSpreadClass(t.spread, t.max_spread);

      $("ticker-time").textContent =
        t.time ? t.time.substring(11, 19) + " UTC" : "—";
      $("ticker-age").textContent = ageString(t.time);
    } catch (e) {
      tickerErrors++;
      if (tickerErrors > 3) setTickerState("error", "offline");
    }
  }

  function setTickerState(cls, label) {
    const el = $("ticker-state");
    el.className = "ticker-state " + cls;
    el.textContent = label;
  }

  setInterval(pollTicker, 300);
  pollTicker();


  function renderHeader(snap) {
    $("last-update").textContent =
      `Updated ${snap.last_heartbeat ? App.fmtTimeShort(snap.last_heartbeat) + " UTC" : "—"}`;
    $("m-balance").textContent = App.fmtUsd(snap.balance);
    $("m-balance-source").textContent = snap.balance_source || "—";
    $("m-equity").textContent = App.fmtUsd(snap.equity);
    const delta = snap.equity - snap.starting_equity;
    const d = $("m-equity-delta");
    d.textContent = (delta >= 0 ? "+" : "") + App.fmtUsd(delta);
    d.className = "metric-delta " + (delta >= 0 ? "positive" : "negative");
    $("m-peak").textContent = App.fmtUsd(snap.peak_equity);
    $("m-drawdown").textContent = App.fmtPct(snap.drawdown_pct);
    $("m-risk").textContent = App.fmtPct(snap.monitor.active_risk_pct);
    $("m-status").textContent = snap.status.toUpperCase();
  }

  function renderPlan(snap) {
    const pm = snap.premarket;
    const body = $("plan-body");
    const banner = $("event-block-banner");
    if (!pm) {
      body.innerHTML = '<div class="muted">Pre-market context will appear once the bot starts.</div>';
      banner.classList.add("d-none");
      return;
    }
    $("plan-date").textContent = "Today's plan — " + pm.date;
    if (pm.event_blocked) {
      banner.classList.remove("d-none");
      banner.textContent = "🚫 Trading blocked today: " + pm.event_reason;
    } else {
      banner.classList.add("d-none");
    }
    const smaStatus = pm.prev_close > pm.sma200_daily
      ? '<span class="text-success">ABOVE SMA200 — long-term up-trend</span>'
      : '<span class="text-danger">BELOW SMA200 — shorts allowed</span>';
    const ext = [];
    if (pm.filter_c_active) ext.push("<b>C</b> TP1 → 50% (low vol)");
    if (pm.long_filter_f || pm.short_filter_f)
      ext.push(`<b>F</b> TP2 → 127% (${pm.long_filter_f ? "long" : "short"})`);
    if (pm.back_to_back_active) ext.push("<b>Opt 2</b> back-to-back TP2 +10%");
    if (pm.high_atr_extension_active) ext.push("<b>Opt 3</b> high-ATR TP2 115%");
    const extLine = ext.length ? '<div class="muted small mt-2">Active: ' + ext.join(" · ") + "</div>" : "";

    body.innerHTML = `
      <div class="row g-3">
        <div class="col-md-6">
          <h3 class="h6">Market context</h3>
          <ul class="list-unstyled small">
            <li><strong>Yesterday close:</strong> ${App.fmtUsd(pm.prev_close)}</li>
            <li><strong>Yesterday range:</strong> ${App.fmtUsd(pm.range)}</li>
            <li><strong>20-day ATR:</strong> ${App.fmtUsd(pm.atr_20)}</li>
            <li><strong>200-day SMA:</strong> ${App.fmtUsd(pm.sma200_daily)} — ${smaStatus}</li>
            <li><strong>Regime:</strong> ${pm.regime} (${pm.regime_ratio})</li>
            <li><strong>Trend bias (4H):</strong> ${pm.trend_bias}</li>
            <li><strong>Seasonal mult (long):</strong> ${pm.seasonal_mult_long}×</li>
            <li><strong>Yesterday's outcome:</strong> ${pm.prev_session_close_type || "—"}</li>
          </ul>
          ${extLine}
        </div>
        <div class="col-md-6">
          <h3 class="h6">Trade levels</h3>
          <table class="level-table"><thead><tr><th></th><th>Long</th><th>Short</th></tr></thead>
          <tbody>
            <tr><th>Entry</th><td>${App.fmtUsd(pm.long_entry)}</td><td>${App.fmtUsd(pm.short_entry)}</td></tr>
            <tr><th>Stop loss</th><td>${App.fmtUsd(pm.long_sl)}</td><td>${App.fmtUsd(pm.short_sl)}</td></tr>
            <tr><th>TP1</th><td>${App.fmtUsd(pm.long_tp1)}</td><td>${App.fmtUsd(pm.short_tp1)}</td></tr>
            <tr><th>TP2 (${pm.long_tp2_fib}× / ${pm.short_tp2_fib}×)</th>
                <td>${App.fmtUsd(pm.long_tp2)}</td><td>${App.fmtUsd(pm.short_tp2)}</td></tr>
          </tbody></table>
          <div class="muted small mt-2">
            Session: ${App.fmtTimeShort(pm.session_start_utc)} → ${App.fmtTimeShort(pm.session_end_utc)} UTC ·
            20:55 UTC partial-close · 21:00 UTC hard stop
          </div>
        </div>
      </div>`;
  }

  function renderPosition(snap) {
    const body = $("position-body");
    const p = snap.position;
    if (!p) {
      body.innerHTML = '<div class="muted">No open position.</div>';
      return;
    }
    const accel = p.accelerated_tp1
      ? `<div class="muted small">⏰ ${p.accelerated_kind === "WEDNESDAY"
            ? "Wednesday TP1 acceleration (70% at 14:30 NY)"
            : "TP1 acceleration (80% at 15:30 NY)"}</div>`
      : "";
    const trim = p.rsi_trim_done ? '<div class="muted small">✂️ RSI post-TP1 trim check done</div>' : "";
    const tranches = p.tranches.map(t => {
      const status = t.open
        ? '<span class="badge bg-success">OPEN</span>'
        : `<span class="badge bg-secondary">CLOSED @ ${App.fmtUsd(t.close_price)} (${t.close_reason})</span>`;
      const partials = (t.partial_closes || []).map(pc =>
        `<div class="muted small ms-3">↳ ${pc.trim_lots} lots (${pc.reason}) @ RSI ${pc.rsi_at_trim}</div>`
      ).join("");
      return `<tr><td>${t.name}</td><td>${t.lots}</td><td>${status}${partials}</td></tr>`;
    }).join("");
    body.innerHTML = `
      <div class="row g-3 mb-2">
        <div class="col"><div class="metric-card">
          <div class="metric-label">Direction</div><div class="metric-value">${p.direction}</div></div></div>
        <div class="col"><div class="metric-card">
          <div class="metric-label">Entry</div><div class="metric-value">${App.fmtUsd(p.entry_price)}</div></div></div>
        <div class="col"><div class="metric-card">
          <div class="metric-label">Current stop</div><div class="metric-value">${App.fmtUsd(p.current_stop)}</div></div></div>
        <div class="col"><div class="metric-card">
          <div class="metric-label">TP1</div><div class="metric-value">${App.fmtUsd(p.tp1)}</div>
          <div class="metric-delta">${p.tp1_hit ? "✓ hit" : "pending"}</div></div></div>
        <div class="col"><div class="metric-card">
          <div class="metric-label">TP2</div><div class="metric-value">${App.fmtUsd(p.tp2)}</div>
          <div class="metric-delta">${p.tp2_hit ? "✓ hit" : "pending"}</div></div></div>
      </div>
      ${accel}${trim}
      <table class="table table-sm mt-2">
        <thead><tr><th>Half</th><th>Lots</th><th>Status</th></tr></thead>
        <tbody>${tranches}</tbody>
      </table>`;
  }

  function metricCard(label, value) {
    return `<div class="col-md-2 col-sm-4 col-6"><div class="metric-card">
      <div class="metric-label">${label}</div>
      <div class="metric-value">${value}</div></div></div>`;
  }

  function renderWeek(snap) {
    const w = snap.week || {};
    $("week-grid").innerHTML = [
      metricCard("Trades", w.trades || 0),
      metricCard("TP1 wins", w.wins_tp1 || 0),
      metricCard("TP2 wins", w.wins_tp2 || 0),
      metricCard("Stop-outs", w.sls || 0),
      metricCard("Regime exits", w.regime_exits || 0),
      metricCard("TP1 accels", w.tp1_accelerations || 0),
    ].join("");
    $("gates-grid").innerHTML = [
      metricCard("News events", w.gate_block_event || 0),
      metricCard("ATR floor", w.gate_block_atr_floor || 0),
      metricCard("ATR cap", w.gate_block_atr_cap || 0),
      metricCard("Wrong trend (4H)", w.gate_block_trend || 0),
      metricCard("SMA200 short", w.gate_block_sma200 || 0),
      metricCard("Already traded", w.gate_block_daily_lock || 0),
    ].join("");
    $("v32-grid").innerHTML = [
      metricCard("Wednesday accels", w.wednesday_accelerations || 0),
      metricCard("COMEX exits", w.comex_vol_exits || 0),
      metricCard("RSI trims", w.rsi_trims || 0),
      metricCard("B2B TP2", w.back_to_back_tp2 || 0),
      metricCard("High-ATR TP2", w.high_atr_tp2 || 0),
      metricCard("20:55 half2", w.session_close_half2 || 0),
    ].join("");
  }

  function renderMonitors(snap) {
    const m = snap.monitor || {};
    const brakes = m.slow_active ? "Slow" : (m.fast_active ? "Fast" : "None");
    $("monitor-grid").innerHTML = [
      metricCard("Win rate fast (20)",
        m.win_rate_fast_20 != null ? m.win_rate_fast_20 + "%" : "n/a"),
      metricCard("Win rate slow (50)",
        m.win_rate_slow_50 != null ? m.win_rate_slow_50 + "%" : "n/a"),
      metricCard("Consecutive losses", m.consecutive_losses || 0),
      metricCard("Brakes active", brakes),
    ].join("");
  }

  function renderEvents(snap) {
    const tbody = $("events-body");
    const events = (snap.recent_events || []).slice(-20).reverse();
    if (events.length === 0) {
      tbody.innerHTML = '<tr><td colspan="3" class="muted">No events yet.</td></tr>';
      return;
    }
    tbody.innerHTML = events.map(e => {
      const ts = (e.ts || "").substring(0, 19).replace("T", " ");
      const kind = e.kind || "";
      const detail = Object.entries(e)
        .filter(([k]) => k !== "ts" && k !== "kind")
        .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`)
        .join(", ")
        .substring(0, 140);
      return `<tr><td><code>${ts}</code></td><td><code>${kind}</code></td><td class="muted small">${detail}</td></tr>`;
    }).join("");
  }

  function levelCard(label, value, sub = "", cls = "") {
    return `<div class="col-md-2 col-sm-4 col-6"><div class="metric-card">
      <div class="metric-label">${label}</div>
      <div class="metric-value ${cls}">${value}</div>
      ${sub ? `<div class="metric-delta">${sub}</div>` : ""}
    </div></div>`;
  }

  function delta(level, ref) {
    if (level == null || ref == null) return "";
    const d = level - ref;
    const sign = d >= 0 ? "+" : "";
    return `${sign}${d.toFixed(2)} from ${App.fmtUsd(ref)}`;
  }

  function renderLevels(snap) {
    const grid = $("levels-grid");
    const src = $("levels-source");
    const pm = snap.premarket;
    if (!pm) {
      grid.innerHTML = '<div class="col text-muted small">Levels will appear once today\'s plan is built.</div>';
      src.textContent = "—";
      return;
    }

    let direction, entry, sl, tp1, tp2, tp2_fib, source;
    if (snap.position && !snap.position.closed) {
      const p = snap.position;
      direction = p.direction;
      entry = p.entry_price;
      sl = p.current_stop;
      tp1 = p.tp1;
      tp2 = p.tp2;
      tp2_fib = direction === "LONG" ? pm.long_tp2_fib : pm.short_tp2_fib;
      source = `Live position (${direction})`;
    } else if (pm.trend_bias === "LONG_ONLY") {
      direction = "LONG"; entry = pm.long_entry; sl = pm.long_sl;
      tp1 = pm.long_tp1; tp2 = pm.long_tp2; tp2_fib = pm.long_tp2_fib;
      source = "Today's plan — long bias";
    } else if (pm.trend_bias === "SHORT_ONLY") {
      direction = "SHORT"; entry = pm.short_entry; sl = pm.short_sl;
      tp1 = pm.short_tp1; tp2 = pm.short_tp2; tp2_fib = pm.short_tp2_fib;
      source = "Today's plan — short bias";
    } else {
      // BOTH zone — favour long if no other signal; show the long side
      direction = "LONG (ambiguous)"; entry = pm.long_entry; sl = pm.long_sl;
      tp1 = pm.long_tp1; tp2 = pm.long_tp2; tp2_fib = pm.long_tp2_fib;
      source = "Today's plan — half size (BOTH zone)";
    }

    src.textContent = source;
    const isLong = direction.startsWith("LONG");
    const slSub = delta(sl, entry);
    const tp1Sub = delta(tp1, entry);
    const tp2Sub = `${(tp2_fib || 1.0).toFixed(3)}× range · ${delta(tp2, entry)}`;

    grid.innerHTML = [
      levelCard(`ATR(${pm.atr_short_period || 20})`, App.fmtUsd(pm.atr_20),
                 `regime ${pm.regime}`),
      levelCard(`ATR(${pm.atr_long_period || 50})`, App.fmtUsd(pm.atr_50),
                 `ratio ${pm.regime_ratio}`),
      levelCard(`Entry (${direction})`, App.fmtUsd(entry),
                 isLong ? "buy on cross above" : "sell on cross below"),
      levelCard("Stop loss", App.fmtUsd(sl), slSub, "text-danger"),
      levelCard("TP1", App.fmtUsd(tp1), tp1Sub, "text-success"),
      levelCard("TP2", App.fmtUsd(tp2), tp2Sub, "text-success"),
    ].join("");
  }

  function renderPending(snap) {
    const banner = $("pending-entry");
    const pe = snap.pending_entry;
    if (!pe) {
      banner.classList.add("d-none");
      return;
    }
    banner.classList.remove("d-none");
    $("pending-countdown").textContent =
      `expires in ${pe.remaining_seconds.toFixed(1)}s`;
    const cell = (l, v) =>
      `<div class="pending-cell"><div class="label">${l}</div><div class="value">${v}</div></div>`;
    $("pending-grid").innerHTML = [
      cell("Direction", pe.direction),
      cell("Entry", "$" + pe.entry_price.toFixed(2)),
      cell("Stop loss", "$" + pe.sl.toFixed(2)),
      cell("TP1", "$" + pe.tp1.toFixed(2)),
      cell("TP2", "$" + pe.tp2.toFixed(2)),
      cell("Lots", (pe.half_1_lots + pe.half_2_lots).toFixed(2) +
            ` (H1 ${pe.half_1_lots.toFixed(2)} / H2 ${pe.half_2_lots.toFixed(2)})`),
      cell("Intended risk", "$" + pe.risk_amount.toFixed(2)),
      cell("Actual risk", "$" + pe.actual_risk.toFixed(2) +
            ` (${pe.deviation_pct.toFixed(1)}% off)`),
    ].join("");
  }

  async function postConfirm(action) {
    const r = await fetch(`/api/control/${action}`, { method: "POST" });
    if (!r.ok) {
      alert(`${action} failed: HTTP ${r.status}`);
      return;
    }
    // The next snapshot poll will refresh the banner; trigger one immediately
    App.fetchOnce();
  }

  document.getElementById("btn-confirm-trade")
    ?.addEventListener("click", () => postConfirm("confirm_trade"));
  document.getElementById("btn-cancel-trade")
    ?.addEventListener("click", () => postConfirm("cancel_trade"));

  App.onSnapshot(snap => {
    renderHeader(snap);
    renderLevels(snap);
    renderPlan(snap);
    renderPending(snap);
    renderPosition(snap);
    renderWeek(snap);
    renderMonitors(snap);
    renderEvents(snap);
  });
})();

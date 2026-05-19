/* Dashboard: render snapshots in real time. */

(function () {
  const $ = id => document.getElementById(id);

  function renderHeader(snap) {
    $("last-update").textContent =
      `Updated ${snap.last_heartbeat ? App.fmtTimeShort(snap.last_heartbeat) + " UTC" : "—"}`;
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

  App.onSnapshot(snap => {
    renderHeader(snap);
    renderPlan(snap);
    renderPosition(snap);
    renderWeek(snap);
    renderMonitors(snap);
    renderEvents(snap);
  });
})();

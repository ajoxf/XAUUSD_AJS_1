/* Backtest page: run + chart + table. */

(function () {
  const $ = id => document.getElementById(id);
  let lastResult = null;

  function isoToday() { return new Date().toISOString().slice(0, 10); }
  function isoDaysAgo(days) {
    const d = new Date(); d.setDate(d.getDate() - days);
    return d.toISOString().slice(0, 10);
  }

  function applyQuickRange() {
    const days = parseInt($("bt-quick").value, 10);
    $("bt-end").value = isoToday();
    $("bt-start").value = isoDaysAgo(days);
  }
  $("bt-quick").addEventListener("change", applyQuickRange);
  applyQuickRange();

  $("bt-form").addEventListener("submit", async e => {
    e.preventDefault();
    $("bt-spinner").classList.remove("d-none");
    $("bt-error").classList.add("d-none");
    $("bt-results").classList.add("d-none");
    $("bt-run").disabled = true;
    const body = {
      start: $("bt-start").value,
      end: $("bt-end").value,
      starting_equity: $("bt-form").elements["starting_equity"].value,
    };
    try {
      const r = await fetch("/api/backtest", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const data = await r.json();
      if (!r.ok) {
        $("bt-error").textContent = data.error || "Unknown error";
        $("bt-error").classList.remove("d-none");
        return;
      }
      lastResult = data;
      render(data);
      $("bt-results").classList.remove("d-none");
    } catch (e) {
      $("bt-error").textContent = "Network error: " + e;
      $("bt-error").classList.remove("d-none");
    } finally {
      $("bt-spinner").classList.add("d-none");
      $("bt-run").disabled = false;
    }
  });

  function render(d) {
    $("bt-n").textContent = d.n_trades;
    $("bt-wr").textContent = d.tp1_win_rate + "%";
    $("bt-final").textContent = App.fmtUsd(d.final_equity);
    $("bt-return").textContent = d.total_return_pct + "%";
    $("bt-dd").textContent = d.max_drawdown_pct + "%";

    const dates = d.equity_curve.map(p => p.date);
    const equity = d.equity_curve.map(p => p.equity);
    let peak = -Infinity;
    const peaks = equity.map(v => (peak = Math.max(peak, v)));
    const drawdowns = equity.map((v, i) => peaks[i] > 0 ? (v - peaks[i]) / peaks[i] * 100 : 0);

    Plotly.newPlot("bt-equity-chart", [
      { x: dates, y: equity, mode: "lines", name: "Equity",
        line: { color: "#10b981", width: 2 } },
      { x: dates, y: peaks, mode: "lines", name: "Peak",
        line: { color: "#3b82f6", width: 1, dash: "dot" }, opacity: 0.4 },
    ], {
      margin: { l: 60, r: 30, t: 20, b: 50 },
      xaxis: { title: "Date" }, yaxis: { title: "Equity (USD)" },
      hovermode: "x unified", template: "plotly_white",
    }, { responsive: true });

    Plotly.newPlot("bt-dd-chart", [
      { x: dates, y: drawdowns, mode: "lines", fill: "tozeroy",
        line: { color: "#ef4444", width: 1 }, name: "Drawdown" },
    ], {
      margin: { l: 60, r: 30, t: 20, b: 50 },
      xaxis: { title: "Date" }, yaxis: { title: "Drawdown %" },
      template: "plotly_white",
    }, { responsive: true });

    const tbody = $("bt-trades-body");
    if (!d.trades.length) {
      tbody.innerHTML = '<tr><td colspan="12" class="muted">No trades.</td></tr>';
      return;
    }
    tbody.innerHTML = d.trades.map(t => `
      <tr>
        <td>${t.date}</td><td>${t.direction}</td><td>${t.regime}</td>
        <td>${t.entry.toFixed(2)}</td><td>${t.sl.toFixed(2)}</td>
        <td>${t.tp1.toFixed(2)}</td><td>${t.tp2.toFixed(2)}</td>
        <td>${t.lots.toFixed(2)}</td>
        <td><span class="outcome-${t.outcome}">${t.outcome}</span></td>
        <td>${t.exit_price.toFixed(2)}</td>
        <td class="${t.pnl >= 0 ? "pnl-positive" : "pnl-negative"}">${App.fmtUsd(t.pnl)}</td>
        <td>${App.fmtUsd(t.equity_after)}</td>
      </tr>`).join("");
  }

  $("bt-download").addEventListener("click", () => {
    if (!lastResult) return;
    const header = "date,direction,regime,entry,sl,tp1,tp2,lots,outcome,exit,pnl,equity_after\n";
    const csv = lastResult.trades.map(t =>
      [t.date, t.direction, t.regime, t.entry, t.sl, t.tp1, t.tp2,
       t.lots, t.outcome, t.exit_price, t.pnl, t.equity_after].join(",")
    ).join("\n");
    const blob = new Blob([header + csv], { type: "text/csv" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = `backtest_${$("bt-start").value}_${$("bt-end").value}.csv`;
    a.click();
    URL.revokeObjectURL(url);
  });
})();

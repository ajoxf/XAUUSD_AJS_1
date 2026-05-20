/* Shared sidebar control + SSE manager. Used on every page. */

const App = (function () {
  let eventSource = null;
  let listeners = [];
  let lastSnapshot = null;

  function fmtUsd(v) {
    if (v == null) return "—";
    return "$" + Number(v).toLocaleString(undefined,
      { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  function fmtPct(v, places = 2) {
    if (v == null) return "—";
    return Number(v).toFixed(places) + "%";
  }
  function fmtTimeShort(iso) {
    if (!iso) return "—";
    return iso.substring(11, 19);
  }

  function applyStatus(snap) {
    const badge = document.getElementById("status-badge");
    if (badge) {
      badge.className = "status-badge " + snap.status;
      const icons = { running: "🟢", starting: "🟡", error: "🔴",
                       completed: "🔵", stopped: "⚪" };
      badge.textContent = `${icons[snap.status] || "⚪"} ${snap.status.toUpperCase()}`;
    }
    setText("stat-equity", fmtUsd(snap.equity));
    setText("stat-dd", fmtPct(snap.drawdown_pct));
    setText("stat-risk", fmtPct(snap.monitor.active_risk_pct, 2));
    setText("stat-heartbeat", fmtTimeShort(snap.last_heartbeat) + " UTC");
    const start = document.getElementById("btn-start");
    const stop = document.getElementById("btn-stop");
    if (start) start.disabled = snap.status === "running" || snap.status === "starting";
    if (stop) stop.disabled = snap.status === "stopped" || snap.status === "completed";
    const eb = document.getElementById("error-banner");
    if (eb) {
      if (snap.last_error) {
        eb.classList.remove("d-none");
        eb.textContent = snap.last_error.slice(0, 400);
      } else {
        eb.classList.add("d-none");
      }
    }
    applyMt5(snap.mt5 || {});
  }

  function applyMt5(m) {
    const el = document.getElementById("mt5-status");
    if (!el) return;
    el.className = "mt5-status";
    if (!m.connected) {
      el.classList.add(m.kind === "paper" ? "" : "down");
      if (m.kind === "paper") {
        el.innerHTML = "MT5: not used (paper)";
      } else {
        el.innerHTML = "MT5: 🔴 not connected" +
          (m.error ? `<span class="sub">${m.error}</span>` : "");
      }
      el.title = m.error || "";
      return;
    }
    // Connected — check algo trading + terminal link
    const algoOff = m.algo_trading_allowed === false;
    const termDown = m.terminal_connected === false;
    if (algoOff || termDown) {
      el.classList.add("warn");
    } else {
      el.classList.add("ok");
    }
    const dry = m.dryrun ? " · DRYRUN" : "";
    let line = `MT5: 🟢 ${m.login} (${m.trade_mode})${dry}`;
    const subs = [];
    if (m.company) subs.push(m.company);
    if (m.server) subs.push(m.server);
    if (algoOff) subs.push("⚠ Algo Trading OFF — orders will be rejected");
    if (termDown) subs.push("⚠ terminal not connected to broker");
    el.innerHTML = line + (subs.length ? `<span class="sub">${subs.join(" · ")}</span>` : "");
    el.title = `${m.company || ""} ${m.server || ""} ${m.currency || ""}`.trim();
  }

  function setText(id, text) {
    const el = document.getElementById(id);
    if (el) el.textContent = text;
  }

  function notifyListeners(snap) {
    listeners.forEach(fn => {
      try { fn(snap); } catch (e) { console.error(e); }
    });
  }

  function startStream() {
    if (eventSource) return;
    eventSource = new EventSource("/api/stream");
    eventSource.addEventListener("snapshot", e => {
      const snap = JSON.parse(e.data);
      lastSnapshot = snap;
      applyStatus(snap);
      notifyListeners(snap);
    });
    eventSource.addEventListener("error", e => {
      console.warn("SSE error", e);
    });
  }

  async function fetchOnce() {
    try {
      const r = await fetch("/api/status");
      if (!r.ok) return;
      const snap = await r.json();
      lastSnapshot = snap;
      applyStatus(snap);
      notifyListeners(snap);
    } catch (e) {
      console.warn("fetch status failed", e);
    }
  }

  async function control(action) {
    const r = await fetch(`/api/control/${action}`, { method: "POST" });
    if (!r.ok) {
      alert(`Failed to ${action}: ${r.status}`);
      return;
    }
    await fetchOnce();
  }

  function wireControls() {
    const start = document.getElementById("btn-start");
    const stop = document.getElementById("btn-stop");
    if (start) start.addEventListener("click", () => control("start"));
    if (stop) stop.addEventListener("click", () => control("stop"));
  }

  function onSnapshot(fn) {
    listeners.push(fn);
    if (lastSnapshot) fn(lastSnapshot);
  }

  document.addEventListener("DOMContentLoaded", () => {
    wireControls();
    fetchOnce().then(() => startStream());
  });

  return { onSnapshot, fetchOnce, fmtUsd, fmtPct, fmtTimeShort };
})();
window.App = App;

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

    // Algo kill switch — independent of Start/Stop
    const algoToggle = document.getElementById("algo-toggle");
    const algoLabel = document.getElementById("algo-toggle-label");
    const algoHint = document.getElementById("algo-toggle-hint");
    if (algoToggle && typeof snap.algo_enabled === "boolean") {
      if (!algoToggle.dataset.armed) {
        algoToggle.checked = snap.algo_enabled;
      } else {
        // Server is the source of truth — re-sync if it diverged
        algoToggle.checked = snap.algo_enabled;
      }
      if (algoLabel) {
        algoLabel.textContent = snap.algo_enabled
          ? "Algo enabled"
          : "🛑 Algo DISABLED";
        algoLabel.className = snap.algo_enabled ? "" : "text-danger fw-bold";
      }
      if (algoHint) {
        algoHint.textContent = snap.algo_enabled
          ? "Scanning for new entries."
          : "New entries blocked. Open positions still managed.";
      }
    }

    const eb = document.getElementById("error-banner");
    if (eb) {
      if (snap.last_error) {
        eb.classList.remove("d-none");
        eb.textContent = snap.last_error.slice(0, 400);
      } else {
        eb.classList.add("d-none");
      }
    }
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

  async function setAlgo(enabled) {
    try {
      const r = await fetch("/api/control/algo", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      if (!r.ok) {
        const body = await r.text();
        alert(`Algo toggle failed: ${body}`);
        return;
      }
      await fetchOnce();
    } catch (e) {
      alert("Algo toggle network error: " + e);
    }
  }

  function wireControls() {
    const start = document.getElementById("btn-start");
    const stop = document.getElementById("btn-stop");
    if (start) start.addEventListener("click", () => control("start"));
    if (stop) stop.addEventListener("click", () => control("stop"));
    const algoToggle = document.getElementById("algo-toggle");
    if (algoToggle) {
      algoToggle.dataset.armed = "1";
      algoToggle.addEventListener("change", () => setAlgo(algoToggle.checked));
    }
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

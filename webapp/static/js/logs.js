/* Logs page. */

(function () {
  const $ = id => document.getElementById(id);
  let knownKinds = new Set();

  async function load() {
    const kind = $("log-kind").value;
    const limit = $("log-limit").value;
    const q = new URLSearchParams();
    if (kind) q.set("kind", kind);
    if (limit) q.set("limit", limit);
    const r = await fetch("/api/logs?" + q.toString());
    if (!r.ok) {
      $("logs-body").innerHTML = `<tr><td colspan="3" class="text-danger">Failed: ${r.status}</td></tr>`;
      return;
    }
    const data = await r.json();
    const events = (data.events || []).slice().reverse();
    events.forEach(e => { if (e.kind) knownKinds.add(e.kind); });
    refreshKindSelect();
    if (events.length === 0) {
      $("logs-body").innerHTML = '<tr><td colspan="3" class="muted">No events.</td></tr>';
      return;
    }
    $("logs-body").innerHTML = events.map(e => {
      const ts = (e.ts || "").substring(0, 19).replace("T", " ");
      const detail = Object.entries(e)
        .filter(([k]) => k !== "ts" && k !== "kind")
        .map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : v}`)
        .join(", ").substring(0, 300);
      return `<tr><td><code>${ts}</code></td><td><code>${e.kind || ""}</code></td>
              <td class="muted small">${detail}</td></tr>`;
    }).join("");
  }

  function refreshKindSelect() {
    const sel = $("log-kind");
    const current = sel.value;
    const opts = ['<option value="">All</option>'];
    [...knownKinds].sort().forEach(k => {
      opts.push(`<option value="${k}" ${k === current ? "selected" : ""}>${k}</option>`);
    });
    sel.innerHTML = opts.join("");
  }

  $("log-refresh").addEventListener("click", load);
  $("log-kind").addEventListener("change", load);
  $("log-limit").addEventListener("change", load);
  load();
})();

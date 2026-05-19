/* Settings page handlers. */

(function () {
  const toast = document.getElementById("save-toast");

  function showToast(msg, isError = false) {
    toast.textContent = msg;
    toast.className = `alert ${isError ? "alert-danger" : "alert-success"}`;
    toast.classList.remove("d-none");
    setTimeout(() => toast.classList.add("d-none"), 2500);
  }

  document.getElementById("save-settings").addEventListener("click", async () => {
    const fd = new FormData();
    for (const form of ["settings-form", "risk-form", "mt5-form"]) {
      const formEl = document.getElementById(form);
      new FormData(formEl).forEach((v, k) => fd.append(k, v));
    }
    const obj = Object.fromEntries(fd.entries());
    if (obj.risk_pct_percent) {
      obj.risk_pct = (parseFloat(obj.risk_pct_percent) / 100).toString();
      delete obj.risk_pct_percent;
    }
    // Unchecked checkboxes are absent from FormData — send explicit booleans
    // so the API can reliably toggle them off.
    const confirmCb = document.getElementById("require_trade_confirmation");
    if (confirmCb) obj.require_trade_confirmation = confirmCb.checked;
    try {
      const r = await fetch("/api/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(obj),
      });
      if (!r.ok) {
        const err = await r.text();
        showToast("Save failed: " + err, true);
        return;
      }
      showToast("Settings saved. They apply next time the bot starts.");
    } catch (e) {
      showToast("Network error: " + e, true);
    }
  });

  document.querySelectorAll(".flag-toggle").forEach(cb => {
    cb.addEventListener("change", async () => {
      const key = cb.dataset.flag;
      const body = { [key]: cb.checked };
      try {
        const r = await fetch("/api/flags", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify(body),
        });
        if (!r.ok) {
          cb.checked = !cb.checked;
          showToast("Flag update failed", true);
          return;
        }
      } catch (e) {
        cb.checked = !cb.checked;
        showToast("Flag update failed: " + e, true);
      }
    });
  });
})();

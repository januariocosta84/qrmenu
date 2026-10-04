/* Order status page: live updates via WebSocket, polling fallback for flaky networks. */
(function () {
  "use strict";
  const CONFIG = JSON.parse(document.getElementById("status-config").textContent);
  const FINAL = ["completed", "cancelled"];
  let current = CONFIG.status;
  let wsAlive = false;

  function onStatus(status, paymentStatus) {
    const paymentChanged = paymentStatus && paymentStatus !== CONFIG.paymentStatus;
    if (!paymentChanged && (!status || status === current)) return;
    if (status === "ready" && navigator.vibrate) navigator.vibrate([200, 100, 200]);
    // Re-render server-side so text, steps and translations stay in one place.
    sessionStorage.setItem("status-scroll", String(window.scrollY));
    location.reload();
  }

  try {
    const y = sessionStorage.getItem("status-scroll");
    if (y) { window.scrollTo(0, parseInt(y, 10)); sessionStorage.removeItem("status-scroll"); }
  } catch (_) { /* ignore */ }

  // Keep listening after "Completed" until the order is paid.
  if (current === "cancelled" || (FINAL.includes(current) && CONFIG.paymentStatus === "paid")) return;

  function connect(delay) {
    if (!("WebSocket" in window)) return;
    const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + CONFIG.wsPath);
    ws.onopen = () => { wsAlive = true; delay = 1000; };
    ws.onmessage = (e) => {
      try {
        const msg = JSON.parse(e.data);
        if (msg.event === "order_status") onStatus(msg.data.status, msg.data.payment_status);
      } catch (_) { /* ignore */ }
    };
    ws.onclose = () => {
      wsAlive = false;
      setTimeout(() => connect(Math.min(delay * 2, 30000)), delay);
    };
  }
  connect(1000);

  // Poll when the socket is down (and once on tab focus) so status is never stale.
  async function poll() {
    try {
      const res = await fetch(CONFIG.apiUrl, { headers: { Accept: "application/json" }, cache: "no-store" });
      if (res.ok) { const d = await res.json(); onStatus(d.status, d.payment_status); }
    } catch (_) { /* offline: try later */ }
  }
  setInterval(() => { if (!wsAlive && !document.hidden) poll(); }, 15000);
  document.addEventListener("visibilitychange", () => { if (!document.hidden) poll(); });
})();

/* Kitchen Display System: three live columns driven by the dashboard socket. */
(function () {
  "use strict";
  const D = window.DASH;
  const CFG = JSON.parse(document.getElementById("kds-config").textContent);
  const $ = (s, r = document) => r.querySelector(s);
  const orders = new Map();
  const ACTIVE = ["new", "accepted", "preparing", "ready"];
  const column = (status) => (status === "accepted" ? "new" : status);

  const statusUrl = (id) => CFG.statusUrl.replace("/0/status/", `/${id}/status/`);
  const money = (n) => CFG.currencySymbol + Number(n).toFixed(2);

  function el(tag, cls, text) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function elapsed(since) {
    if (!since) return "";
    const s = Math.max(0, Math.floor((Date.now() - new Date(since).getTime()) / 1000));
    const h = Math.floor(s / 3600);
    const m = String(Math.floor((s % 3600) / 60)).padStart(2, "0");
    const sec = String(s % 60).padStart(2, "0");
    return h ? `${h}:${m}:${sec}` : `${m}:${sec}`;
  }

  function card(o) {
    const col = column(o.status);
    const c = el("article", `kcard kcard-${col}`);
    c.dataset.id = o.id;

    const head = el("header", "kcard-head");
    const title = el("h3", null, `#${o.number} — ${o.table_number ? "Table " + o.table_number : "Counter"}`);
    const paid = o.payment_status === "paid";
    title.appendChild(el("span", `kpay kpay-${paid ? "paid" : "unpaid"}`, paid ? "PAID" : "UNPAID"));
    const since = col === "preparing" ? o.preparing_at : col === "ready" ? o.ready_at : o.created_at;
    const timer = el("span", "timer", elapsed(since));
    timer.dataset.since = since || "";
    if (col === "preparing" && o.estimated_minutes) timer.dataset.limit = o.estimated_minutes * 60;
    head.append(title, timer);
    c.appendChild(head);

    if (o.customer_name) {
      const cust = el("p", "kcard-cust");
      cust.innerHTML = D.icon("user");
      cust.appendChild(document.createTextNode(o.customer_name));
      c.appendChild(cust);
    }
    if (o.source === "staff") {
      const by = el("span", "kby");
      by.innerHTML = D.icon("pen");
      by.appendChild(document.createTextNode(`by waiter${o.placed_by_name ? " " + o.placed_by_name : ""}`));
      c.appendChild(by);
    }

    const ul = el("ul", "kitems");
    o.items.forEach((i) => {
      const li = el("li");
      li.appendChild(el("strong", null, `${i.name} × ${i.quantity}`));
      i.options.forEach((op) => li.appendChild(el("div", "kopt", `+ ${op.name}`)));
      if (i.note) li.appendChild(el("div", "knote", `“${i.note}”`));
      ul.appendChild(li);
    });
    c.appendChild(ul);
    if (o.note) {
      const n = el("div", "kcard-note");
      n.appendChild(el("strong", null, "Customer note: "));
      n.appendChild(document.createTextNode(o.note));
      c.appendChild(n);
    }

    const foot = el("div", "kcard-actions");
    const btn = (label, status, cls = "btn-primary") => {
      const b = el("button", `btn ${cls}`, label);
      b.type = "button";
      b.addEventListener("click", () => act(o, status, b));
      return b;
    };
    if (col === "new") {
      foot.appendChild(btn("ACCEPT ORDER", "preparing", "btn-primary btn-lg"));
      if (CFG.canCancel) foot.appendChild(btn("Cancel", "cancelled", "btn-ghost"));
    } else if (col === "preparing") {
      foot.appendChild(btn("MARK AS READY", "ready", "btn-success btn-lg"));
      if (CFG.canCancel) foot.appendChild(btn("Cancel", "cancelled", "btn-ghost"));
    } else if (col === "ready") {
      foot.appendChild(btn("COMPLETED", "completed", "btn-dark btn-lg"));
    }
    if (CFG.canPay && !paid) {
      const pay = el("button", "btn btn-sm");
      pay.innerHTML = D.icon("cash");
      pay.appendChild(document.createTextNode(` Mark paid · ${money(o.total)}`));
      pay.type = "button";
      pay.addEventListener("click", () => markPaid(o, pay));
      foot.appendChild(pay);
    }
    const meta = el("div", "kcard-meta muted small", `${o.items.reduce((s, i) => s + i.quantity, 0)} items · ${money(o.total)}`);
    c.append(foot, meta);
    return c;
  }

  async function act(order, status, button) {
    let note = "";
    if (status === "cancelled") {
      note = prompt(`Cancel order #${order.number}? Reason (optional):`, "");
      if (note === null) return;
    }
    button.disabled = true;
    try {
      upsert(await D.api(statusUrl(order.id), "POST", { status, note }));
    } catch (err) {
      D.toast(err.message || "Could not update the order.", null, "error");
      button.disabled = false;
      load();
    }
  }

  async function markPaid(order, button) {
    const dueCents = Math.round(parseFloat(order.total) * 100);
    const tendered = await D.cashDialog({ title: `Order #${order.number} — cash payment`, dueCents });
    if (tendered === null) return;
    button.disabled = true;
    try {
      const res = await D.api(CFG.paidUrl.replace("/0/payments/", `/${order.id}/payments/`), "POST", { tendered });
      upsert(res.order);
      const rc = res.receipt || {};
      D.paymentDone({
        changeCents: Math.round(parseFloat(res.change || "0") * 100),
        tenderedCents: Math.round(parseFloat(res.tendered) * 100),
        dueCents, orderIds: rc.orders || [], mode: rc.mode, printed: rc.printed,
      });
      if (res.drawer && res.drawer.opened) D.toast(`Cash drawer opened · #${order.number} paid`, null, "new");
      else if (res.drawer && res.drawer.attempted) D.toast(`Paid, but the drawer did not open: ${res.drawer.message}`, null, "error");
    } catch (err) {
      D.toast(err.message || "Could not record the payment.", null, "error");
      button.disabled = false;
    }
  }

  function upsert(o, isNew) {
    if (!ACTIVE.includes(o.status)) orders.delete(o.id);
    else orders.set(o.id, Object.assign(o, isNew ? { _new: Date.now() } : {}));
    render();
  }

  function render() {
    const cols = { new: [], preparing: [], ready: [] };
    [...orders.values()]
      .sort((a, b) => new Date(a.created_at) - new Date(b.created_at))
      .forEach((o) => cols[column(o.status)] && cols[column(o.status)].push(o));
    Object.entries(cols).forEach(([name, list]) => {
      const box = $(`#col-${name}`);
      box.replaceChildren(...list.map((o) => {
        const c = card(o);
        if (o._new && Date.now() - o._new < 8000) c.classList.add("flash");
        return c;
      }));
      if (!list.length) box.appendChild(el("p", "kempty", "—"));
      $(`#count-${name}`).textContent = list.length;
    });
  }

  async function load() {
    try {
      const data = await D.api(CFG.ordersUrl);
      orders.clear();
      data.forEach((o) => orders.set(o.id, o));
      render();
      $("#kds-status").textContent = `Updated ${new Date().toLocaleTimeString()} · new orders appear automatically.`;
    } catch (_) {
      $("#kds-status").textContent = "Could not load orders — retrying…";
      setTimeout(load, 5000);
    }
  }

  document.addEventListener("dash:message", (e) => {
    const { event, data } = e.detail;
    if (event === "new_order") upsert(data, true);
    else if (event === "order_updated") upsert(data);
  });
  document.addEventListener("dash:reconnect", load); // catch up on anything missed while offline

  // Timers
  setInterval(() => {
    document.querySelectorAll(".timer").forEach((t) => {
      t.textContent = elapsed(t.dataset.since);
      if (t.dataset.limit && t.dataset.since) {
        const secs = (Date.now() - new Date(t.dataset.since).getTime()) / 1000;
        t.classList.toggle("late", secs > Number(t.dataset.limit));
      }
    });
  }, 1000);
  // Safety net in case a socket event is lost.
  setInterval(load, 60000);

  // Controls
  const soundBtn = $("#sound-toggle");
  const syncSound = () => (soundBtn.innerHTML = D.soundEnabled() ? D.icon("volume") + " Sound on" : D.icon("volume-off") + " Enable sound");
  soundBtn.addEventListener("click", () => { D.setSound(!D.soundEnabled()); syncSound(); });
  syncSound();
  $("#fullscreen").addEventListener("click", () => {
    if (document.fullscreenElement) document.exitFullscreen();
    else document.documentElement.requestFullscreen && document.documentElement.requestFullscreen();
  });

  load();
})();

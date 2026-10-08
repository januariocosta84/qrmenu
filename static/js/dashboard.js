/* Shared dashboard behaviour: live socket, new-order alerts, notification bell. */
(function () {
  "use strict";
  const D = window.DASH;
  if (!D) return;

  const $ = (s) => document.querySelector(s);

  /**
   * Translate a dashboard string with Django's JS catalog (/jsi18n/), e.g.
   * D.t("Table %(n)s", { n: 4 }). Falls back to English if the catalog is missing.
   */
  D.t = function (text, vars) {
    const s = typeof window.gettext === "function" ? window.gettext(text) : text;
    return vars ? s.replace(/%\((\w+)\)s/g, (m, k) => (k in vars ? vars[k] : m)) : s;
  };
  const _ = D.t;

  D.csrf = function () {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  };

  D.api = async function (url, method = "GET", body) {
    const res = await fetch(url, {
      method,
      credentials: "same-origin",
      headers: { Accept: "application/json", "Content-Type": "application/json", "X-CSRFToken": D.csrf() },
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw Object.assign(new Error(data.message || data.detail || `HTTP ${res.status}`), { data });
    return data;
  };

  /** Inline icon from the page's SVG sprite, e.g. D.icon("cash"). */
  D.icon = (name) => `<svg class="i" aria-hidden="true"><use href="#i-${name}"></use></svg>`;

  // Off-canvas navigation (tablet/phone)
  const navToggle = $("#nav-toggle");
  const setNav = (open) => {
    document.body.classList.toggle("nav-open", open);
    if (navToggle) navToggle.setAttribute("aria-expanded", String(open));
  };
  if (navToggle) navToggle.addEventListener("click", () => setNav(!document.body.classList.contains("nav-open")));
  document.querySelectorAll("[data-nav-close]").forEach((el) => el.addEventListener("click", () => setNav(false)));
  document.addEventListener("keydown", (e) => { if (e.key === "Escape") setNav(false); });

  // Account menu: close when clicking elsewhere
  document.addEventListener("click", (e) => {
    document.querySelectorAll("details.user-menu[open]").forEach((d) => { if (!d.contains(e.target)) d.open = false; });
  });

  // ---- Sound (browsers require a user gesture before audio can play) ----
  let audioCtx = null;
  D.soundEnabled = () => {
    try { return localStorage.getItem("dash-sound") === "1"; } catch (err) { return false; }
  };
  D.setSound = (on) => {
    try { localStorage.setItem("dash-sound", on ? "1" : "0"); } catch (err) { /* ignore */ }
    if (on) D.beep();
  };
  D.beep = function () {
    try {
      audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
      if (audioCtx.state === "suspended") audioCtx.resume();
      const now = audioCtx.currentTime;
      [0, 0.18, 0.36].forEach((t, i) => {
        const osc = audioCtx.createOscillator();
        const gain = audioCtx.createGain();
        osc.type = "sine";
        osc.frequency.value = i === 2 ? 1175 : 880;
        gain.gain.setValueAtTime(0.0001, now + t);
        gain.gain.exponentialRampToValueAtTime(0.35, now + t + 0.02);
        gain.gain.exponentialRampToValueAtTime(0.0001, now + t + 0.16);
        osc.connect(gain).connect(audioCtx.destination);
        osc.start(now + t);
        osc.stop(now + t + 0.17);
      });
    } catch (err) { /* audio unavailable */ }
  };
  document.addEventListener("click", () => { if (audioCtx && audioCtx.state === "suspended") audioCtx.resume(); }, { passive: true });

  // ---- Toasts ----
  D.toast = function (text, href, kind = "info") {
    const box = $("#toasts");
    const el = document.createElement(href ? "a" : "div");
    el.className = `toast toast-${kind}`;
    const iconName = { new: "bell", error: "alert", info: "check-circle" }[kind] || "info";
    el.innerHTML = D.icon(iconName);
    el.appendChild(document.createTextNode(text));
    if (href) el.href = href;
    box.appendChild(el);
    setTimeout(() => el.classList.add("hide"), 6000);
    setTimeout(() => el.remove(), 6600);
  };

  // ---- Cash dialog: amount received → change to give ----
  const toCents = (v) => Math.round(parseFloat(String(v).replace(",", ".")) * 100);
  const fmt = (c) => (D.currencySymbol || "$") + (c / 100).toFixed(2);

  function quickAmounts(dueC) {
    const out = [];
    [100, 500, 1000, 2000, 5000, 10000].forEach((step) => {
      const v = Math.ceil(dueC / step) * step;
      if (v > dueC && !out.includes(v)) out.push(v);
    });
    return out.slice(0, 5);
  }

  /** Ask how much cash the customer gave. Resolves to the tendered amount (string) or null if cancelled. */
  D.cashDialog = function ({ title, dueCents }) {
    return new Promise((resolve) => {
      const wrap = document.createElement("div");
      wrap.className = "cash-modal";
      wrap.innerHTML = `
        <div class="cash-box" role="dialog" aria-modal="true" aria-labelledby="cash-title">
          <h2 id="cash-title"></h2>
          <div class="cash-due"><span>${_("Total due")}</span><strong data-due></strong></div>
          <label class="cash-label" for="cash-in">${_("Cash received from customer")}</label>
          <input id="cash-in" class="cash-input" inputmode="decimal" autocomplete="off">
          <div class="cash-quick">
            <button type="button" class="btn" data-exact>${_("Exact")}</button>
          </div>
          <div class="cash-change"><span>${_("Change to give")}</span><strong data-change></strong></div>
          <p class="err" data-err hidden></p>
          <div class="cash-actions">
            <button type="button" class="btn btn-lg" data-cancel>${_("Cancel")}</button>
            <button type="button" class="btn btn-success btn-lg" data-ok>${_("Paid")}</button>
          </div>
        </div>`;
      const $w = (s) => wrap.querySelector(s);
      $w("#cash-title").textContent = title;
      $w("[data-due]").textContent = fmt(dueCents);
      const input = $w("#cash-in");
      input.value = (dueCents / 100).toFixed(2);
      const quick = $w(".cash-quick");
      quickAmounts(dueCents).forEach((c) => {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "btn";
        b.textContent = fmt(c);
        b.addEventListener("click", () => { input.value = (c / 100).toFixed(2); update(); });
        quick.appendChild(b);
      });
      $w("[data-exact]").addEventListener("click", () => { input.value = (dueCents / 100).toFixed(2); update(); });

      function update() {
        const c = toCents(input.value);
        const ok = Number.isFinite(c) && c >= dueCents;
        const change = ok ? c - dueCents : 0;
        $w("[data-change]").textContent = ok ? fmt(change) : "—";
        $w(".cash-change").classList.toggle("has-change", ok && change > 0);
        const err = $w("[data-err]");
        err.hidden = ok || !input.value;
        if (!ok && Number.isFinite(c)) err.textContent = _("Not enough: %(amount)s short.", { amount: fmt(dueCents - c) });
        else if (!ok) err.textContent = _("Enter the amount received.");
        $w("[data-ok]").disabled = !ok;
        $w("[data-ok]").innerHTML = D.icon("cash") + " " + (ok && change > 0 ? _("Paid · give %(amount)s change", { amount: fmt(change) }) : _("Paid · exact amount"));
      }
      function close(value) {
        wrap.remove();
        document.removeEventListener("keydown", onKey);
        resolve(value);
      }
      function onKey(e) {
        if (e.key === "Escape") close(null);
        if (e.key === "Enter" && !$w("[data-ok]").disabled) close((toCents(input.value) / 100).toFixed(2));
      }
      input.addEventListener("input", update);
      $w("[data-cancel]").addEventListener("click", () => close(null));
      $w("[data-ok]").addEventListener("click", () => close((toCents(input.value) / 100).toFixed(2)));
      wrap.addEventListener("click", (e) => { if (e.target === wrap) close(null); });
      document.addEventListener("keydown", onKey);
      document.body.appendChild(wrap);
      update();
      input.focus();
      input.select();
    });
  };

  /**
   * Print a receipt for these order ids: straight to the network receipt
   * printer when configured, otherwise via this device's print dialog.
   * Call from a click handler (phones only allow opening the print window then).
   */
  D.printReceipt = async function (orderIds) {
    const ids = (orderIds || []).join(",");
    if (!ids) return;
    if (D.receiptNetwork) {
      try {
        const res = await D.api(D.receiptPrintApi, "POST", { orders: orderIds });
        D.toast(res.message || _("Receipt printed."));
      } catch (err) {
        D.toast(_("Receipt not printed: %(reason)s", { reason: err.message }), null, "error");
      }
      return;
    }
    const url = `${D.receiptUrl}?orders=${encodeURIComponent(ids)}&autoprint=1`;
    const touch = window.matchMedia("(pointer: coarse)").matches;
    if (touch) {  // mobile browsers print iframes unreliably: use a window that prints itself
      if (!window.open(url, "_blank")) location.href = url;
      return;
    }
    const old = document.getElementById("receipt-frame");
    if (old) old.remove();
    const frame = document.createElement("iframe");
    frame.id = "receipt-frame";
    frame.title = _("Receipt");
    frame.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden";
    frame.onload = () => {
      try {
        frame.contentWindow.focus();
        frame.contentWindow.print();
      } catch (err) {  // frame blocked or not printable: print from its own window instead
        frame.remove();
        if (!window.open(url, "_blank")) location.href = url;
      }
    };
    frame.src = url;
    document.body.appendChild(frame);
  };

  /**
   * After a payment: show the change to give (cash) and, depending on the
   * restaurant's setting, ask "Print receipt?" (ask), print on OK (always),
   * or just confirm (never).
   */
  D.paymentDone = function ({ changeCents = null, tenderedCents = null, dueCents = null, orderIds = [], mode = D.receiptMode, printed = null } = {}) {
    const ask = mode === "ask" && orderIds.length;
    const always = mode === "always" && orderIds.length && !printed;
    const wrap = document.createElement("div");
    wrap.className = "cash-modal";
    wrap.innerHTML = `<div class="cash-box change-result" role="alertdialog" aria-modal="true" aria-labelledby="pd-q">
      <div data-cash hidden><p class="cash-label"></p><p class="change-big"></p><p class="muted" data-detail></p></div>
      <div data-paid hidden><p class="change-big">${D.icon("check-circle")}</p><p class="cash-label">${_("PAID")}</p></div>
      <div data-ask hidden class="receipt-ask"><p id="pd-q" class="receipt-q">${D.icon("printer")} ${_("Print receipt?")}</p></div>
      <div class="cash-actions" data-actions></div></div>`;
    const $w = (s) => wrap.querySelector(s);
    if (changeCents !== null) {
      $w("[data-cash]").hidden = false;
      $w("[data-cash] .cash-label").textContent = changeCents > 0 ? _("GIVE CHANGE") : _("EXACT AMOUNT");
      $w("[data-cash] .change-big").textContent = changeCents > 0 ? fmt(changeCents) : "✓";
      $w("[data-detail]").textContent = _("Received %(received)s · Total %(total)s", { received: fmt(tenderedCents), total: fmt(dueCents) });
    } else {
      $w("[data-paid]").hidden = false;
    }
    const actions = $w("[data-actions]");
    const close = () => { wrap.remove(); document.removeEventListener("keydown", onKey); };
    const button = (cls, html, fn) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = `btn btn-lg ${cls}`;
      b.innerHTML = html;
      b.addEventListener("click", () => { close(); if (fn) fn(); });
      actions.appendChild(b);
      return b;
    };
    let primary;
    if (ask) {
      $w("[data-ask]").hidden = false;
      button("", _("No"), null);
      primary = button("btn-primary", `${D.icon("printer")} ${_("Yes, print")}`, () => D.printReceipt(orderIds));
    } else if (always) {
      primary = button("btn-primary btn-block", `${D.icon("printer")} ${_("OK · print receipt")}`, () => D.printReceipt(orderIds));
    } else {
      primary = button("btn-primary btn-block", _("OK"), null);
    }
    if (printed) D.toast(printed.printed ? _("Receipt printed.") : _("Receipt not printed: %(reason)s", { reason: printed.message }), null, printed.printed ? "info" : "error");
    function onKey(e) {
      if (e.key === "Escape") close();
      if (ask && (e.key === "y" || e.key === "Y")) { close(); D.printReceipt(orderIds); }
      if (ask && (e.key === "n" || e.key === "N")) close();
    }
    document.addEventListener("keydown", onKey);
    document.body.appendChild(wrap);
    primary.focus();
  };

  /** Back-compat: change pop-up without a receipt question. */
  D.showChange = (changeCents, tenderedCents, dueCents) =>
    D.paymentDone({ changeCents, tenderedCents, dueCents, mode: "never" });

  // After a page reload: turn the server's change amounts ("change,received,total")
  // and receipt marker into one pop-up ("Give change $6.00 · Print receipt? No / Yes").
  {
    const amounts = document.querySelector("[data-change-amounts]");
    const marker = document.querySelector("[data-receipt-orders]");
    const orderIds = marker ? marker.dataset.receiptOrders.split(",").map(Number).filter(Boolean) : [];
    const m = amounts ? amounts.dataset.changeAmounts.split(",").map(toCents) : null;
    if (m && m.length === 3) D.paymentDone({ changeCents: m[0], tenderedCents: m[1], dueCents: m[2], orderIds });
    else if (orderIds.length) D.paymentDone({ orderIds });
  }

  // Any element with data-print-receipt="12,13" prints that receipt on click.
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-print-receipt]");
    if (!el) return;
    e.preventDefault();
    D.printReceipt(el.dataset.printReceipt.split(",").map(Number).filter(Boolean));
  });

  // Forms marked data-cash-due ask for the cash received before submitting.
  document.addEventListener("submit", async (e) => {
    const form = e.target;
    if (!form.matches("form[data-cash-due]") || form.dataset.cashConfirmed) return;
    e.preventDefault();
    const dueCents = toCents(form.dataset.cashDue) - toCents(form.dataset.cashPaid || "0");
    const tendered = await D.cashDialog({ title: form.dataset.cashTitle || _("Cash payment"), dueCents });
    if (tendered === null) return;
    let field = form.querySelector("input[name=tendered]");
    if (!field) {
      field = document.createElement("input");
      field.type = "hidden";
      field.name = "tendered";
      form.appendChild(field);
    }
    field.value = tendered;
    if (e.submitter && e.submitter.name) {  // keep which button was pressed (e.g. "Paid & free the table")
      const extra = document.createElement("input");
      extra.type = "hidden";
      extra.name = e.submitter.name;
      extra.value = e.submitter.value;
      form.appendChild(extra);
    }
    form.dataset.cashConfirmed = "1";
    form.submit();
  });

  // ---- Bell ----
  const bell = $("#bell-count");
  let unread = 0;
  function setUnread(n) {
    unread = n;
    bell.textContent = n > 99 ? "99+" : n;
    bell.hidden = n === 0;
  }
  fetch(D.notificationsUrl, { credentials: "same-origin", headers: { Accept: "application/json" } })
    .then((r) => (r.ok ? r.json() : null))
    .then((d) => d && setUnread(d.unread))
    .catch(() => {});

  // ---- Title flash for new orders while tab is in background ----
  const baseTitle = document.title;
  let flashTimer = null;
  function flashTitle(text) {
    if (!document.hidden) return;
    clearInterval(flashTimer);
    let on = false;
    flashTimer = setInterval(() => { document.title = (on = !on) ? text : baseTitle; }, 1000);
  }
  document.addEventListener("visibilitychange", () => {
    if (!document.hidden) { clearInterval(flashTimer); document.title = baseTitle; }
  });

  // ---- Live socket ----
  const conn = $("#conn");
  let everConnected = false;
  function connect(delay) {
    const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + D.wsPath);
    let ping = null;
    ws.onopen = () => {
      conn.classList.add("live");
      conn.title = _("Live: new orders appear automatically");
      const t = conn.querySelector(".live-text");
      if (t) t.textContent = _("Live");
      if (everConnected) document.dispatchEvent(new CustomEvent("dash:reconnect"));
      everConnected = true;
      delay = 1000;
      ping = setInterval(() => ws.readyState === 1 && ws.send(JSON.stringify({ type: "ping" })), 25000);
    };
    ws.onmessage = (e) => {
      let msg;
      try { msg = JSON.parse(e.data); } catch (err) { return; }
      if (msg.event === "new_order") {
        const o = msg.data;
        const where = o.table_number ? _("Table %(n)s", { n: o.table_number }) : _("Counter");
        D.toast(_("New order #%(number)s · %(where)s", { number: o.number, where }), `${D.ordersBase}${o.id}/`, "new");
        setUnread(unread + 1);
        if (D.soundEnabled()) D.beep();
        flashTitle("🔔 " + _("New order #%(number)s", { number: o.number }));
        if (navigator.vibrate) navigator.vibrate(300);
      }
      document.dispatchEvent(new CustomEvent("dash:message", { detail: msg }));
    };
    ws.onclose = () => {
      clearInterval(ping);
      conn.classList.remove("live");
      conn.title = _("Reconnecting…");
      const t = conn.querySelector(".live-text");
      if (t) t.textContent = _("Offline");
      setTimeout(() => connect(Math.min(delay * 2, 30000)), delay);
    };
  }
  if ("WebSocket" in window) connect(1000);
})();

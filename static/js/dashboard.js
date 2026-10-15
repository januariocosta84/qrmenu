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
    D.printPage(`${D.receiptUrl}?orders=${encodeURIComponent(ids)}&autoprint=1`, { fromClick: true });
  };

  /**
   * Print a dashboard page (receipt, drawer slip) on this device's printer.
   * Desktop: a hidden frame + the print dialog (silent with Chrome --kiosk-printing).
   * Phones/tablets print frames unreliably, so after a tap we open a window that prints itself;
   * without a tap (e.g. right after a page load) a new window would be blocked, so we use the frame.
   */
  D.printPage = function (url, { fromClick = false } = {}) {
    const touch = window.matchMedia("(pointer: coarse)").matches;
    if (touch && fromClick) {
      if (!window.open(url, "_blank")) location.href = url;
      return;
    }
    const old = document.getElementById("print-frame");
    if (old) old.remove();
    const frame = document.createElement("iframe");
    frame.id = "print-frame";
    frame.title = _("Receipt");
    frame.style.cssText = "position:fixed;right:0;bottom:0;width:0;height:0;border:0;visibility:hidden";
    frame.onload = () => {
      try {
        frame.contentWindow.focus();
        frame.contentWindow.print();
      } catch (err) {  // frame blocked or not printable: print from its own window instead
        frame.remove();
        if (fromClick && !window.open(url, "_blank")) location.href = url;
      }
    };
    frame.src = url;
    document.body.appendChild(frame);
  };

  /** Printer-driver cash drawer: print a small slip; the printer driver opens the drawer. */
  D.printDrawerSlip = function (reason, orderIds = [], opts = {}) {
    const q = new URLSearchParams({ reason: reason || "payment", orders: (orderIds || []).join(","), autoprint: "1" });
    D.printPage(`${D.drawerSlipUrl}?${q}`, opts);
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

  // After a page reload: print a pending drawer slip (printer-driver cash drawer).
  {
    const slip = document.querySelector("[data-drawer-slip]");
    const receiptPending = document.querySelector("[data-receipt-orders]") && D.receiptMode === "always" && !D.receiptNetwork;
    if (slip && !(slip.dataset.drawerSlip.startsWith("payment:") && receiptPending)) {
      const [reason, ids] = slip.dataset.drawerSlip.split(":");
      D.printDrawerSlip(reason, (ids || "").split(",").filter(Boolean));
    }
  }

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

  /**
   * Print bill dialog (table bills with café + car wash): choose the services to bill, one combined
   * bill or one per service, paper 58/80 mm and copies; Preview, Print bill, or take cash and print.
   * scope: {session} (a table's open bill), {plate} (walk-in car) or {orders: "1,2"}.
   */
  D.billDialog = async function (scope, title) {
    let opts;
    try {
      opts = await D.api(`${D.billOptionsUrl}?${new URLSearchParams(scope)}`);
    } catch (err) {
      D.toast(err.message, null, "error");
      return;
    }
    const money = (v) => opts.currency + Number(v).toFixed(2);
    const wrap = document.createElement("div");
    wrap.className = "cash-modal";
    wrap.innerHTML = `<div class="cash-box bill-box" role="dialog" aria-modal="true" aria-labelledby="bill-title">
      <h2 id="bill-title"></h2>
      <div data-body></div>
      <p class="err" data-err hidden></p>
      <div class="cash-actions" data-actions></div></div>`;
    const $w = (sel) => wrap.querySelector(sel);
    $w("#bill-title").innerHTML = D.icon("printer") + " ";
    $w("#bill-title").appendChild(document.createTextNode(title || _("Print bill")));
    const close = () => { wrap.remove(); document.removeEventListener("keydown", onKey); };
    const onKey = (e) => { if (e.key === "Escape") close(); };
    document.addEventListener("keydown", onKey);
    wrap.addEventListener("click", (e) => { if (e.target === wrap) close(); });
    const body = $w("[data-body]");
    const actions = $w("[data-actions]");

    if (!opts.services.length) {
      body.innerHTML = `<p>${_("Nothing to bill: everything here is paid.")}</p><p class="muted small">${_("Reprint a bill from the list of printed bills.")}</p>`;
      const ok = document.createElement("button");
      ok.type = "button"; ok.className = "btn btn-primary btn-lg btn-block"; ok.textContent = _("OK");
      ok.addEventListener("click", close);
      actions.appendChild(ok);
      document.body.appendChild(wrap);
      ok.focus();
      return;
    }

    const rows = opts.services.map((s) => `<label class="bill-svc"><input type="checkbox" data-svc value="${s.code}" checked>
        <span><strong></strong><small class="muted"></small></span><span class="bill-amt">${money(s.total)}</span></label>`).join("");
    body.innerHTML = `
      <fieldset class="bill-group"><legend>${_("What to bill (unpaid only)")}</legend>${rows}</fieldset>
      <fieldset class="bill-group" data-mode-box><legend>${_("Print mode")}</legend>
        <label class="radio"><input type="radio" name="bill-mode" value="combined"> ${_("One combined bill")}</label>
        <label class="radio"><input type="radio" name="bill-mode" value="separate"> ${_("Separate bill per service")}</label></fieldset>
      <div class="bill-row">
        <fieldset class="bill-group"><legend>${_("Paper")}</legend>
          <label class="radio"><input type="radio" name="bill-width" value="32"> 58 mm</label>
          <label class="radio"><input type="radio" name="bill-width" value="48"> 80 mm</label></fieldset>
        <label class="bill-group bill-copies">${_("Copies")} <input type="number" min="1" max="5" value="1" data-copies></label>
      </div>
      <div class="cash-due"><span>${_("Total")}</span><strong data-total></strong></div>`;
    opts.services.forEach((s, i) => {
      const row = body.querySelectorAll(".bill-svc")[i];
      row.querySelector("strong").textContent = s.name;
      row.querySelector("small").textContent = s.plates.length ? ` · ${_("Plate")}: ${s.plates.join(", ")}` : "";
    });
    body.querySelector(`[name=bill-mode][value=${opts.mode}]`).checked = true;
    (body.querySelector(`[name=bill-width][value="${opts.width}"]`) || body.querySelector("[name=bill-width][value='48']")).checked = true;

    const chosen = () => opts.services.filter((s) => body.querySelector(`[data-svc][value=${s.code}]`).checked);
    const params = () => {
      const svc = chosen();
      return {
        orders: svc.flatMap((s) => s.orders),
        mode: body.querySelector("[name=bill-mode]:checked").value,
        width: body.querySelector("[name=bill-width]:checked").value,
        copies: Math.max(1, Math.min(5, parseInt(body.querySelector("[data-copies]").value, 10) || 1)),
        totalCents: svc.reduce((t, s) => t + Math.round(Number(s.total) * 100), 0),
      };
    };
    const refresh = () => {
      const p = params();
      $w("[data-total]").textContent = money(p.totalCents / 100);
      body.querySelector("[data-mode-box]").hidden = chosen().length < 2;
      actions.querySelectorAll("[data-needs-orders]").forEach((b) => (b.disabled = !p.orders.length));
    };
    body.addEventListener("change", refresh);

    const button = (cls, html, fn, needsOrders = true) => {
      const b = document.createElement("button");
      b.type = "button";
      b.className = `btn btn-lg ${cls}`;
      b.innerHTML = html;
      if (needsOrders) b.dataset.needsOrders = "1";
      b.addEventListener("click", fn);
      actions.appendChild(b);
      return b;
    };
    const showError = (msg) => { const e = $w("[data-err]"); e.textContent = msg; e.hidden = false; };

    async function submit(pay) {
      const p = params();
      let tendered = null;
      if (pay) {
        tendered = await D.cashDialog({ title: title || _("Cash payment"), dueCents: p.totalCents });
        if (tendered === null) return;
      }
      actions.querySelectorAll("button").forEach((b) => (b.disabled = true));
      try {
        const res = await D.api(opts.printUrl, "POST", { orders: p.orders, mode: p.mode, width: p.width, copies: p.copies, pay, tendered });
        if (res.printed) D.toast(res.printed.printed ? _("Bill printed.") : _("Bill not printed: %(reason)s", { reason: res.printed.message }), null, res.printed.printed ? "info" : "error");
        else D.printPage(res.printUrl, { fromClick: true });
        const numbers = res.receipts.map((x) => x.number).join(", ");
        body.innerHTML = res.change
          ? `<div class="cash-change has-change"><span>${Number(res.change.change) > 0 ? _("GIVE CHANGE") : _("EXACT AMOUNT")}</span><strong>${Number(res.change.change) > 0 ? money(res.change.change) : "✓"}</strong></div>
             <p class="muted">${_("Received %(received)s · Total %(total)s", { received: money(res.change.tendered), total: money(res.change.due) })}</p>`
          : "";
        body.insertAdjacentHTML("beforeend", `<p>${D.icon("check-circle")} ${_("Printed: %(numbers)s", { numbers })}</p>`);
        actions.innerHTML = "";
        button("btn-primary btn-block", _("Done"), () => { close(); location.reload(); }, false).focus();
      } catch (err) {
        showError(err.message);
        actions.querySelectorAll("button").forEach((b) => (b.disabled = false));
      }
    }

    button("", `${D.icon("search")} ${_("Preview")}`, () => {
      const p = params();
      const q = new URLSearchParams({ orders: p.orders.join(","), mode: p.mode, width: p.width });
      window.open(`${opts.previewUrl}?${q}`, "_blank");
    });
    const printBtn = button("btn-primary", `${D.icon("printer")} ${_("Print bill")}`, () => submit(false));
    if (opts.canPay) button("btn-success", `${D.icon("cash")} ${_("Paid (cash) & print")}`, () => submit(true));
    document.body.appendChild(wrap);
    refresh();
    printBtn.focus();
  };

  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-bill-dialog]");
    if (!el) return;
    e.preventDefault();
    D.billDialog(JSON.parse(el.dataset.billDialog), el.dataset.billTitle);
  });

  /** Reprint a stored bill (marked COPY). */
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-reprint]");
    if (!el) return;
    e.preventDefault();
    D.printPage(el.dataset.reprint, { fromClick: true });
  });

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
        if (o.service_type === "car_wash") {
          D.toast(_("Car wash #%(number)s · %(where)s · Plate %(plate)s", { number: o.number, where, plate: o.plate_number }),
                  `${D.ordersBase}${o.id}/`, "new");
        } else {
          D.toast(_("New order #%(number)s · %(where)s", { number: o.number, where }), `${D.ordersBase}${o.id}/`, "new");
        }
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

  // ---- Order alarms ----
  // New orders: the alarm repeats on every staff screen until someone accepts the order.
  // Ready orders: waiters/cashiers hear a different chime until the food is picked up.
  // Only for food the kitchen prepares (not car wash). The Kitchen screen has its own buttons.
  (function () {
    const box = $("#order-alarm");
    if (!box || !D.alarmOrdersUrl) return;
    const onKds = document.body.classList.contains("kds-page");
    const wantsReady = D.role && D.role !== "kitchen" && !onKds;
    const isKitchenFood = (o) => !o.service_type || (D.kitchenServices || ["cafe"]).includes(o.service_type);
    const waiting = new Map(); // status "new"
    const ready = new Map(); // status "ready"
    const NEW_EVERY = 5000, READY_EVERY = 15000;
    let lastNew = 0, lastReady = 0;
    const statusUrl = (id) => D.statusUrl.replace("/0/status/", `/${id}/status/`);
    const where = (o) => (o.table_number ? _("Table %(n)s", { n: o.table_number }) : _("Counter"));

    D.chime = function () { // "ding-dong": lower and longer than the new-order beep
      try {
        audioCtx = audioCtx || new (window.AudioContext || window.webkitAudioContext)();
        if (audioCtx.state === "suspended") audioCtx.resume();
        const now = audioCtx.currentTime;
        [[0, 784], [0.42, 523]].forEach(([t, f]) => {
          const osc = audioCtx.createOscillator(), gain = audioCtx.createGain();
          osc.type = "triangle";
          osc.frequency.value = f;
          gain.gain.setValueAtTime(0.0001, now + t);
          gain.gain.exponentialRampToValueAtTime(0.45, now + t + 0.03);
          gain.gain.exponentialRampToValueAtTime(0.0001, now + t + 0.8);
          osc.connect(gain).connect(audioCtx.destination);
          osc.start(now + t);
          osc.stop(now + t + 0.85);
        });
      } catch (err) { /* audio unavailable */ }
    };

    function track(o, fromSocket) {
      if (!o || !o.id || !isKitchenFood(o)) return;
      const wasReady = ready.has(o.id);
      waiting.delete(o.id);
      ready.delete(o.id);
      if (o.status === "new") waiting.set(o.id, o);
      if (o.status === "ready") ready.set(o.id, o);
      if (fromSocket && o.status === "new") lastNew = Date.now(); // the socket handler already beeped
      if (fromSocket && o.status === "ready" && !wasReady && wantsReady) {
        D.toast(_("Order #%(number)s · %(where)s is ready. Please pick it up.", { number: o.number, where: where(o) }),
                `${D.ordersBase}${o.id}/`, "new");
        flashTitle("🍽 " + _("Ready: #%(number)s", { number: o.number }));
        if (navigator.vibrate) navigator.vibrate([200, 100, 200]);
        if (D.soundEnabled()) D.chime();
        lastReady = Date.now();
      }
      render();
    }

    function row(o, label, status, cls) {
      const li = document.createElement("li");
      const text = document.createElement("a");
      text.href = `${D.ordersBase}${o.id}/`;
      text.textContent = `#${o.number} · ${where(o)}`;
      const b = document.createElement("button");
      b.type = "button";
      b.className = `btn btn-sm ${cls}`;
      b.textContent = label;
      b.addEventListener("click", async () => {
        b.disabled = true;
        try { track(await D.api(statusUrl(o.id), "POST", { status })); }
        catch (err) { D.toast(err.message || _("Could not update the order."), null, "error"); b.disabled = false; }
      });
      li.append(text, b);
      return li;
    }

    function render() {
      const parts = [];
      if (waiting.size && !onKds) {
        const sec = document.createElement("section");
        sec.className = "alarm-new";
        sec.innerHTML = `<p>${D.icon("bell")} <strong></strong></p><ul></ul>`;
        sec.querySelector("strong").textContent = waiting.size === 1 ? _("1 new order is waiting to be accepted")
          : _("%(n)s new orders are waiting to be accepted", { n: waiting.size });
        const ul = sec.querySelector("ul");
        [...waiting.values()].forEach((o) => ul.appendChild(row(o, _("Accept"), "preparing", "btn-primary")));
        const k = document.createElement("a");
        k.className = "alarm-link";
        k.href = D.kitchenUrl;
        k.textContent = _("Open the kitchen screen");
        sec.appendChild(k);
        parts.push(sec);
      }
      if (ready.size && wantsReady) {
        const sec = document.createElement("section");
        sec.className = "alarm-ready";
        sec.innerHTML = `<p>${D.icon("check-circle")} <strong></strong></p><ul></ul>`;
        sec.querySelector("strong").textContent = _("Ready to pick up");
        const ul = sec.querySelector("ul");
        [...ready.values()].forEach((o) => ul.appendChild(row(o, _("Picked up"), "completed", "btn-success")));
        parts.push(sec);
      }
      if (parts.length && !D.soundEnabled()) {
        const b = document.createElement("button");
        b.type = "button";
        b.className = "btn btn-sm alarm-sound";
        b.innerHTML = D.icon("volume") + " " + _("Turn on the sound");
        b.addEventListener("click", () => { D.setSound(true); render(); });
        parts.push(b);
      }
      box.replaceChildren(...parts);
      box.hidden = !parts.length;
      document.body.classList.toggle("has-alarm", !box.hidden);
    }

    setInterval(() => {
      const now = Date.now();
      if (!D.soundEnabled()) return;
      if (waiting.size && now - lastNew >= NEW_EVERY) {
        lastNew = now;
        D.beep();
        if (navigator.vibrate) navigator.vibrate(300);
      } else if (ready.size && wantsReady && now - lastReady >= READY_EVERY) {
        lastReady = now;
        D.chime();
      }
    }, 1000);

    async function load() {
      try {
        const list = await D.api(D.alarmOrdersUrl);
        waiting.clear();
        ready.clear();
        list.forEach((o) => track(o, false));
        render();
      } catch (err) { /* try again on the next reconnect */ }
    }
    document.addEventListener("dash:message", (e) => {
      const { event, data } = e.detail;
      if (event === "new_order" || event === "order_updated") track(data, true);
    });
    document.addEventListener("dash:reconnect", load);
    load();
  })();
})();

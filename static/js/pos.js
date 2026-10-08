/* Waiter order entry (POS) for guests without a smartphone. */
(function () {
  "use strict";
  const D = window.DASH;
  const _ = D.t;
  const CFG = JSON.parse(document.getElementById("pos-config").textContent);
  const $ = (s) => document.querySelector(s);
  const el = (tag, cls, text) => {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  };
  const cents = (v) => Math.round(parseFloat(v) * 100);
  const money = (c) => CFG.currencySymbol + (c / 100).toFixed(2);

  const items = {};
  CFG.menu.forEach((c) => c.items.forEach((i) => (items[i.id] = i)));
  let lines = []; // {id, qty, options:[ids], note}
  let sending = false;
  let activeCat = CFG.menu.length ? CFG.menu[0].id : null;

  // ---------- Where: table + guest ----------
  const tableSel = $("#pos-table");
  const guestSel = $("#pos-guest");
  if (CFG.table) tableSel.value = String(CFG.table);

  function fillGuests(selectRef) {
    guestSel.replaceChildren(new Option(_("New guest"), ""));
    (CFG.guests[tableSel.value] || []).forEach((g) => guestSel.add(new Option(g.label, g.ref)));
    guestSel.disabled = !tableSel.value;
    guestSel.value = selectRef && [...guestSel.options].some((o) => o.value === selectRef) ? selectRef : "";
    updateWhere();
  }
  function updateWhere() {
    const t = tableSel.options[tableSel.selectedIndex].text;
    const g = guestSel.value ? guestSel.options[guestSel.selectedIndex].text : (tableSel.value ? "new guest" : "");
    $("#pos-where-label").textContent = `${t}${g ? " · " + g : ""}`;
  }
  tableSel.addEventListener("change", () => fillGuests(""));
  guestSel.addEventListener("change", updateWhere);
  fillGuests(CFG.guest);

  // ---------- Menu ----------
  function renderCats() {
    const box = $("#pos-cats");
    box.replaceChildren(...CFG.menu.map((c) => {
      const b = el("button", "btn btn-sm" + (c.id === activeCat ? " btn-dark" : ""), c.name);
      b.type = "button";
      b.addEventListener("click", () => { activeCat = c.id; $("#pos-search").value = ""; renderCats(); renderItems(); });
      return b;
    }));
  }
  function renderItems() {
    const q = $("#pos-search").value.trim().toLowerCase();
    const list = q
      ? Object.values(items).filter((i) => i.name.toLowerCase().includes(q))
      : (CFG.menu.find((c) => c.id === activeCat) || { items: [] }).items;
    const box = $("#pos-items");
    box.replaceChildren(...list.map((i) => {
      const b = el("button", "pos-item" + (i.is_available ? "" : " soldout"));
      b.type = "button";
      b.disabled = !i.is_available;
      b.appendChild(el("strong", null, i.name));
      b.appendChild(el("span", null, i.is_available ? money(cents(i.price)) : _("Sold out")));
      if (i.options.length) b.appendChild(el("small", "muted", "+ add-ons"));
      b.addEventListener("click", () => (i.options.length ? openItem(i) : addLine(i.id, 1, [], "")));
      return b;
    }));
    if (!list.length) box.appendChild(el("p", "muted", _("No dishes.")));
  }
  $("#pos-search").addEventListener("input", renderItems);

  // ---------- Item modal (add-ons, note, qty) ----------
  const modal = $("#pos-item-modal");
  let modalItem = null;
  let modalQty = 1;
  function openItem(i) {
    modalItem = i;
    modalQty = 1;
    $("#pim-title").textContent = i.name;
    $("#pim-price").textContent = money(cents(i.price));
    $("#pim-note").value = "";
    $("#pim-qty").textContent = "1";
    const box = $("#pim-options");
    box.replaceChildren(...i.options.map((o) => {
      const label = el("label", "pim-opt");
      const cb = el("input");
      cb.type = "checkbox";
      cb.value = o.id;
      cb.disabled = !o.is_available;
      label.append(cb, el("span", null, o.name + (o.is_available ? "" : " (" + _("sold out") + ")")),
                   el("span", "muted", cents(o.price) ? "+" + money(cents(o.price)) : ""));
      return label;
    }));
    modal.hidden = false;
  }
  $("#pim-dec").addEventListener("click", () => { modalQty = Math.max(1, modalQty - 1); $("#pim-qty").textContent = modalQty; });
  $("#pim-inc").addEventListener("click", () => { modalQty = Math.min(50, modalQty + 1); $("#pim-qty").textContent = modalQty; });
  $("#pim-cancel").addEventListener("click", () => (modal.hidden = true));
  modal.addEventListener("click", (e) => { if (e.target === modal) modal.hidden = true; });
  $("#pim-add").addEventListener("click", () => {
    const opts = [...document.querySelectorAll("#pim-options input:checked")].map((c) => Number(c.value));
    addLine(modalItem.id, modalQty, opts, $("#pim-note").value.trim());
    modal.hidden = true;
  });

  // ---------- Ticket ----------
  function lineUnit(l) {
    const i = items[l.id];
    const byId = Object.fromEntries(i.options.map((o) => [o.id, o]));
    return cents(i.price) + l.options.reduce((s, oid) => s + (byId[oid] ? cents(byId[oid].price) : 0), 0);
  }
  function addLine(id, qty, options, note) {
    const key = [id, options.slice().sort().join("."), note].join("|");
    const found = lines.find((l) => l.key === key);
    if (found) found.qty = Math.min(50, found.qty + qty);
    else lines.push({ key, id, qty, options, note });
    renderTicket();
  }
  function renderTicket() {
    const ul = $("#pos-lines");
    ul.replaceChildren(...lines.map((l, idx) => {
      const i = items[l.id];
      const byId = Object.fromEntries(i.options.map((o) => [o.id, o]));
      const li = el("li", "pos-line");
      const info = el("div", "pos-line-info");
      info.appendChild(el("strong", null, i.name));
      if (l.options.length) info.appendChild(el("div", "muted small", "+ " + l.options.map((o) => byId[o] && byId[o].name).join(", ")));
      if (l.note) info.appendChild(el("div", "knote", `“${l.note}”`));
      const qty = el("div", "pos-qty");
      const dec = el("button", "btn btn-sm", "−");
      const inc = el("button", "btn btn-sm", "+");
      dec.type = inc.type = "button";
      dec.addEventListener("click", () => { if (--l.qty < 1) lines.splice(idx, 1); renderTicket(); });
      inc.addEventListener("click", () => { l.qty = Math.min(50, l.qty + 1); renderTicket(); });
      qty.append(dec, el("span", null, String(l.qty)), inc);
      li.append(info, qty, el("span", "pos-line-total", money(lineUnit(l) * l.qty)));
      return li;
    }));
    const sub = lines.reduce((s, l) => s + lineUnit(l) * l.qty, 0);
    const service = Math.round((sub * parseFloat(CFG.serviceChargePercent || "0")) / 100);
    $("#pos-empty").hidden = lines.length > 0;
    $("#pos-subtotal").textContent = money(sub);
    $("#pos-service").textContent = money(service);
    $("#pos-service-row").hidden = service === 0;
    $("#pos-total").textContent = money(sub + service);
    $("#pos-send").disabled = !lines.length || sending || !CFG.acceptingOrders;
    $("#pos-send").innerHTML = D.icon("flame") + " " + _("Send to kitchen") + (lines.length ? ` · ${money(sub + service)}` : "");
  }

  // ---------- Send ----------
  $("#pos-send").addEventListener("click", async () => {
    if (!lines.length || sending) return;
    sending = true;
    renderTicket();
    const err = $("#pos-error");
    err.hidden = true;
    try {
      const order = await D.api(CFG.createUrl, "POST", {
        table: tableSel.value ? Number(tableSel.value) : null,
        guest: guestSel.value,
        customer_name: $("#pos-name").value.trim(),
        note: $("#pos-note").value.trim(),
        items: lines.map((l) => ({ menu_item: l.id, quantity: l.qty, options: l.options, note: l.note })),
      });
      const where = order.table_number ? _("Table %(n)s", { n: order.table_number }) : _("Counter");
      D.toast(_("Order #%(number)s sent to the kitchen · %(where)s", { number: order.number, where }), CFG.orderUrl.replace("/0/", `/${order.id}/`), "new");
      // Keep adding for the same guest if the waiter wants more.
      if (tableSel.value) {
        const list = (CFG.guests[tableSel.value] = CFG.guests[tableSel.value] || []);
        if (!list.some((g) => g.ref === order.guest)) {
          const name = $("#pos-name").value.trim();
          list.push({ ref: order.guest, label: _("Guest %(n)s", { n: list.length + 1 }) + (name ? " · " + name : "") });
        }
        fillGuests(order.guest);
      }
      lines = [];
      $("#pos-note").value = "";
    } catch (e) {
      err.textContent = e.message || _("Could not send the order.");
      err.hidden = false;
      if (e.data && e.data.unavailable) {
        e.data.unavailable.forEach((id) => { if (items[id]) items[id].is_available = false; });
        lines = lines.filter((l) => items[l.id].is_available);
        renderItems();
      }
    }
    sending = false;
    renderTicket();
  });

  if (!CFG.acceptingOrders) {
    $("#pos-error").textContent = _("Ordering is switched off in Restaurant settings.");
    $("#pos-error").hidden = false;
  }
  renderCats();
  renderItems();
  renderTicket();
})();

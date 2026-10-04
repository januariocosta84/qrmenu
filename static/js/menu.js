/* Customer menu: cart, item sheet, checkout, live availability. No dependencies. */
(function () {
  "use strict";

  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));
  const readJSON = (id) => JSON.parse(document.getElementById(id).textContent);

  const CONFIG = readJSON("menu-config");
  const ITEMS = readJSON("items-data");
  const T = readJSON("ui-strings");
  const MAX_QTY = 50;

  const money = (n) => CONFIG.currencySymbol + Number(n).toFixed(2);
  const cents = (s) => Math.round(parseFloat(s) * 100);

  // ---------- Search & category nav (works without ordering) ----------
  const search = $("#search");
  if (search) {
    search.addEventListener("input", () => {
      const q = search.value.trim().toLowerCase();
      let any = false;
      $$(".cat").forEach((sec) => {
        let visible = 0;
        $$(".card", sec).forEach((card) => {
          const hit = !q || card.dataset.search.toLowerCase().includes(q);
          card.hidden = !hit;
          if (hit) visible++;
        });
        sec.hidden = visible === 0;
        any = any || visible > 0;
      });
      $("#no-results").hidden = any;
    });
  }

  // Category tabs. We scroll the page ourselves (instead of relying on the
  // #anchor) and only ever scroll the tab bar *horizontally*: on phones,
  // element.scrollIntoView() also scrolls the page and interrupted the jump,
  // leaving customers stuck on an earlier category.
  const nav = $(".cat-nav");
  const tabBar = $(".cat-scroll");
  const navLinks = $$(".cat-scroll a");
  const byCat = Object.fromEntries(navLinks.map((a) => [a.dataset.cat, a]));
  let jumpingTo = null; // category we are scrolling to after a tap
  let jumpTimer = null;

  const navHeight = () => (nav ? nav.getBoundingClientRect().height : 0);

  function setActive(cat) {
    const link = byCat[cat];
    if (!link || link.classList.contains("active")) return;
    navLinks.forEach((a) => a.classList.remove("active"));
    link.classList.add("active");
    // Centre the tab inside the bar without touching the page's vertical scroll.
    const left = link.offsetLeft - (tabBar.clientWidth - link.offsetWidth) / 2;
    tabBar.scrollTo({ left: Math.max(0, left), behavior: "smooth" });
  }

  function currentSection() {
    const visible = $$(".cat").filter((s) => !s.hidden);
    const atBottom = window.innerHeight + window.scrollY >= document.documentElement.scrollHeight - 4;
    if (atBottom && visible.length) {
      // Short last categories can never reach the top; at the bottom, pick the
      // last one already on screen (or keep the one the customer tapped).
      const tapped = visible.find((s) => byCat[s.dataset.cat] && byCat[s.dataset.cat].classList.contains("active"));
      if (tapped && tapped.getBoundingClientRect().top < window.innerHeight) return tapped;
    }
    const line = navHeight() + 12;
    let current = null;
    $$(".cat").forEach((sec) => {
      if (!sec.hidden && sec.getBoundingClientRect().top <= line) current = sec;
    });
    return current || $$(".cat").find((s) => !s.hidden);
  }

  navLinks.forEach((link) => {
    link.addEventListener("click", (e) => {
      const target = document.getElementById(`cat-${link.dataset.cat}`);
      if (!target) return;
      e.preventDefault();
      jumpingTo = link.dataset.cat;
      setActive(jumpingTo);
      const top = target.getBoundingClientRect().top + window.scrollY - navHeight() - 4;
      window.scrollTo({ top: Math.max(0, top), behavior: "smooth" });
      history.replaceState(null, "", `#cat-${link.dataset.cat}`);
      clearTimeout(jumpTimer);
      jumpTimer = setTimeout(() => (jumpingTo = null), 1200); // fallback if no scroll event arrives
    });
  });

  let ticking = false;
  window.addEventListener("scroll", () => {
    if (ticking) return;
    ticking = true;
    requestAnimationFrame(() => {
      ticking = false;
      if (jumpingTo) {
        // Keep the tapped tab highlighted until the smooth scroll settles.
        clearTimeout(jumpTimer);
        jumpTimer = setTimeout(() => (jumpingTo = null), 150);
        return;
      }
      const sec = currentSection();
      if (sec) setActive(sec.dataset.cat);
    });
  }, { passive: true });

  if (navLinks.length) {
    const sec = currentSection();
    if (sec) setActive(sec.dataset.cat);
  }

  // ---------- Live availability (Sold Out) ----------
  function applyAvailability(id, available) {
    const item = ITEMS[String(id)];
    if (item) item.is_available = available;
    const card = $(`.card[data-item="${id}"]`);
    if (card) {
      card.classList.toggle("sold-out", !available);
      const btn = $("[data-add]", card);
      if (btn) btn.disabled = !available;
    }
    if (CONFIG.canOrder) renderCart();
  }

  function connectSocket(path, onMessage) {
    if (!("WebSocket" in window)) return;
    let delay = 1000;
    const open = () => {
      const ws = new WebSocket((location.protocol === "https:" ? "wss://" : "ws://") + location.host + path);
      ws.onopen = () => (delay = 1000);
      ws.onmessage = (e) => {
        try { onMessage(JSON.parse(e.data)); } catch (_) { /* ignore malformed */ }
      };
      ws.onclose = () => {
        setTimeout(open, delay);
        delay = Math.min(delay * 2, 30000);
      };
    };
    open();
  }

  connectSocket(CONFIG.wsPath, (msg) => {
    if (msg.event === "availability") applyAvailability(msg.data.id, msg.data.is_available);
  });

  // ---------- Ordering ends as soon as this customer's bill is paid ----------
  if (CONFIG.canOrder) {
    let checking = false;
    const checkAccess = async () => {
      if (checking) return;
      checking = true;
      try {
        const res = await fetch(CONFIG.accessUrl, { credentials: "same-origin", cache: "no-store",
                                                    headers: { Accept: "application/json" } });
        if (res.ok && !(await res.json()).can_order) location.reload(); // page shows "bill paid, scan again"
      } catch (_) { /* offline: try later */ }
      checking = false;
    };
    connectSocket("/ws/customer/", (msg) => {
      if (msg.event === "order_status" && msg.data.payment_status === "paid") checkAccess();
    });
    document.addEventListener("visibilitychange", () => { if (!document.hidden) checkAccess(); });
    setInterval(() => { if (!document.hidden) checkAccess(); }, 30000); // fallback if the socket is down
  }

  if (!CONFIG.canOrder) return;

  // ---------- Cart state ----------
  const storage = {
    get() {
      try { return JSON.parse(localStorage.getItem(CONFIG.cartKey)) || []; } catch (_) { return []; }
    },
    set(lines) {
      try { localStorage.setItem(CONFIG.cartKey, JSON.stringify(lines)); } catch (_) { /* private mode */ }
    },
  };
  let cart = storage.get().filter((l) => ITEMS[String(l.id)]);
  let submitting = false;

  const lineKey = (id, options, note) => [id, options.slice().sort().join("."), note].join("|");

  function lineUnitCents(line) {
    const item = ITEMS[String(line.id)];
    if (!item) return 0;
    const byId = Object.fromEntries(item.options.map((o) => [o.id, o]));
    return cents(item.price) + line.options.reduce((s, oid) => s + (byId[oid] ? cents(byId[oid].price) : 0), 0);
  }

  function addToCart(id, qty, options = [], note = "") {
    note = note.trim().slice(0, 200);
    const key = lineKey(id, options, note);
    const existing = cart.find((l) => l.key === key);
    if (existing) existing.qty = Math.min(MAX_QTY, existing.qty + qty);
    else cart.push({ key, id, qty, options, note });
    save();
    toast(`✓ ${ITEMS[String(id)].name} × ${qty}`);
  }

  function save() {
    storage.set(cart);
    renderCart();
  }

  function totals() {
    const sub = cart.reduce((s, l) => s + lineUnitCents(l) * l.qty, 0);
    const service = Math.round((sub * parseFloat(CONFIG.serviceChargePercent || "0")) / 100);
    return { sub, service, total: sub + service, count: cart.reduce((s, l) => s + l.qty, 0) };
  }

  // ---------- Steppers ----------
  function bindStepper(root, onChange) {
    const out = $("[data-qty]", root);
    const get = () => parseInt(out.textContent, 10) || 1;
    const set = (v) => {
      out.textContent = Math.max(1, Math.min(MAX_QTY, v));
      if (onChange) onChange(get());
    };
    $("[data-dec]", root).addEventListener("click", (e) => { e.stopPropagation(); set(get() - 1); });
    $("[data-inc]", root).addEventListener("click", (e) => { e.stopPropagation(); set(get() + 1); });
    return { get, set };
  }

  // Card-level quick add
  $$(".card").forEach((card) => {
    const id = card.dataset.item;
    const stepperEl = $("[data-stepper]", card);
    const stepper = stepperEl ? bindStepper(stepperEl) : null;
    const add = $("[data-add]", card);
    if (add) {
      add.addEventListener("click", () => {
        const item = ITEMS[id];
        if (!item || !item.is_available) return;
        const qty = stepper ? stepper.get() : 1;
        if (item.options.length) openItem(id, qty); // let the customer pick add-ons
        else {
          addToCart(Number(id), qty);
          if (stepper) stepper.set(1);
        }
      });
    }
    $$("[data-open]", card).forEach((el) => el.addEventListener("click", () => openItem(id, stepper ? stepper.get() : 1)));
  });

  // ---------- Sheets ----------
  let lastFocus = null;
  function openSheet(sheet) {
    lastFocus = document.activeElement;
    sheet.hidden = false;
    document.body.classList.add("no-scroll");
    requestAnimationFrame(() => sheet.classList.add("open"));
    const focusable = $(".sheet-close", sheet);
    if (focusable) focusable.focus({ preventScroll: true });
  }
  function closeSheet(sheet) {
    sheet.classList.remove("open");
    document.body.classList.remove("no-scroll");
    setTimeout(() => (sheet.hidden = true), 200);
    if (lastFocus) lastFocus.focus({ preventScroll: true });
  }
  $$(".sheet").forEach((sheet) => {
    $$("[data-close]", sheet).forEach((el) => el.addEventListener("click", () => closeSheet(sheet)));
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") $$(".sheet.open").forEach(closeSheet);
  });

  // Item sheet
  const itemSheet = $("#item-sheet");
  let sheetItemId = null;
  const sheetStepper = bindStepper($("#item-sheet-stepper"), () => updateSheetPrice());

  function selectedOptions() {
    return $$("#item-sheet-option-list input:checked").map((i) => Number(i.value));
  }
  function updateSheetPrice() {
    const unit = lineUnitCents({ id: sheetItemId, options: selectedOptions() });
    $("#item-sheet-add").textContent = `${T.add_to_cart} · ${money((unit * sheetStepper.get()) / 100)}`;
  }

  function openItem(id, qty = 1) {
    const item = ITEMS[String(id)];
    if (!item) return;
    sheetItemId = Number(id);
    $("#item-sheet-title").textContent = item.name;
    $("#item-sheet-desc").textContent = item.description || "";
    $("#item-sheet-price").textContent = money(item.price);
    const img = $("#item-sheet-img");
    img.innerHTML = "";
    if (item.image) {
      const el = document.createElement("img");
      el.src = item.image;
      el.alt = "";
      img.appendChild(el);
    }
    img.hidden = !item.image;
    const list = $("#item-sheet-option-list");
    list.innerHTML = "";
    item.options.forEach((o) => {
      const label = document.createElement("label");
      label.className = "check";
      const input = document.createElement("input");
      input.type = "checkbox";
      input.value = o.id;
      input.disabled = !o.is_available;
      input.addEventListener("change", updateSheetPrice);
      const name = document.createElement("span");
      name.textContent = o.name + (o.is_available ? "" : ` (${T.unavailable})`);
      const price = document.createElement("span");
      price.className = "muted";
      price.textContent = cents(o.price) ? `+${money(o.price)}` : "";
      label.append(input, name, price);
      list.appendChild(label);
    });
    $("#item-sheet-options").hidden = item.options.length === 0;
    $("#item-sheet-note").value = "";
    sheetStepper.set(qty);
    const addBtn = $("#item-sheet-add");
    addBtn.disabled = !item.is_available;
    if (!item.is_available) addBtn.textContent = T.sold_out;
    else updateSheetPrice();
    openSheet(itemSheet);
  }

  $("#item-sheet-add").addEventListener("click", () => {
    addToCart(sheetItemId, sheetStepper.get(), selectedOptions(), $("#item-sheet-note").value);
    const cardStepper = $(`.card[data-item="${sheetItemId}"] [data-qty]`);
    if (cardStepper) cardStepper.textContent = "1";
    closeSheet(itemSheet);
  });

  // ---------- Cart sheet ----------
  const cartSheet = $("#cart-sheet");
  $("#cart-bar").addEventListener("click", () => {
    renderCart();
    openSheet(cartSheet);
  });

  function renderCart() {
    const t = totals();
    const bar = $("#cart-bar");
    bar.hidden = t.count === 0;
    document.body.classList.toggle("has-cart", t.count > 0);
    $("#cart-count").textContent = t.count;
    $("#cart-bar-total").textContent = money(t.total / 100);

    const list = $("#cart-lines");
    list.innerHTML = "";
    let blocked = false;
    cart.forEach((line, idx) => {
      const item = ITEMS[String(line.id)];
      const byId = Object.fromEntries(item.options.map((o) => [o.id, o]));
      const unavailable = !item.is_available || line.options.some((oid) => byId[oid] && !byId[oid].is_available);
      blocked = blocked || unavailable;

      const li = document.createElement("li");
      li.className = "cart-line" + (unavailable ? " unavailable" : "");
      const info = document.createElement("div");
      info.className = "cl-info";
      const name = document.createElement("strong");
      name.textContent = item.name;
      info.appendChild(name);
      if (line.options.length) {
        const opts = document.createElement("div");
        opts.className = "muted small";
        opts.textContent = "+ " + line.options.map((oid) => (byId[oid] ? byId[oid].name : "")).filter(Boolean).join(", ");
        info.appendChild(opts);
      }
      if (line.note) {
        const note = document.createElement("div");
        note.className = "muted small";
        note.textContent = "“" + line.note + "”";
        info.appendChild(note);
      }
      if (unavailable) {
        const warn = document.createElement("div");
        warn.className = "error small";
        warn.textContent = T.sold_out;
        info.appendChild(warn);
      }
      const right = document.createElement("div");
      right.className = "cl-right";
      const price = document.createElement("div");
      price.className = "price";
      price.textContent = money((lineUnitCents(line) * line.qty) / 100);
      const stepper = document.createElement("div");
      stepper.className = "stepper small";
      stepper.innerHTML = '<button type="button" data-dec aria-label="-">−</button><span data-qty></span><button type="button" data-inc aria-label="+">+</button>';
      $("[data-qty]", stepper).textContent = line.qty;
      $("[data-dec]", stepper).addEventListener("click", () => {
        if (line.qty <= 1) cart.splice(idx, 1);
        else line.qty -= 1;
        save();
      });
      $("[data-inc]", stepper).addEventListener("click", () => {
        line.qty = Math.min(MAX_QTY, line.qty + 1);
        save();
      });
      const remove = document.createElement("button");
      remove.type = "button";
      remove.className = "link-btn small";
      remove.textContent = T.remove;
      remove.addEventListener("click", () => {
        cart.splice(idx, 1);
        save();
      });
      right.append(price, stepper, remove);
      li.append(info, right);
      list.appendChild(li);
    });

    $("#cart-empty").hidden = cart.length > 0;
    $("#cart-totals").hidden = cart.length === 0;
    $("#t-subtotal").textContent = money(t.sub / 100);
    $("#t-service").textContent = money(t.service / 100);
    $("#t-service-row").hidden = t.service === 0;
    $("#t-total").textContent = money(t.total / 100);
    $("#place-total").textContent = money(t.total / 100);
    $("#place-order").disabled = cart.length === 0 || blocked || submitting;
  }

  // ---------- Checkout ----------
  const errorEl = $("#checkout-error");

  function csrfToken() {
    const m = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/);
    return m ? decodeURIComponent(m[1]) : "";
  }

  $("#checkout").addEventListener("submit", async (e) => {
    e.preventDefault();
    if (submitting || !cart.length) return;
    const form = e.target;
    const btn = $("#place-order");
    submitting = true;
    btn.disabled = true;
    const label = btn.innerHTML;
    btn.textContent = T.placing_order;
    errorEl.hidden = true;

    const payload = {
      items: cart.map((l) => ({ menu_item: l.id, quantity: l.qty, options: l.options, note: l.note })),
      customer_name: form.customer_name.value.trim(),
      customer_phone: form.customer_phone.value.trim(),
      note: form.note.value.trim(),
      payment_method: form.payment_method.value,
    };
    try {
      const res = await fetch(CONFIG.orderUrl, {
        method: "POST",
        credentials: "same-origin",
        headers: { "Content-Type": "application/json", "X-CSRFToken": csrfToken(), Accept: "application/json" },
        body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (res.status === 201) {
        cart = [];
        storage.set(cart);
        location.href = data.status_url;
        return;
      }
      if (data.error === "scan_required") {
        // Bill paid / table freed while the menu was open: show the scan-again notice.
        storage.set([]);
        location.reload();
        return;
      }
      if (data.unavailable) data.unavailable.forEach((id) => applyAvailability(id, false));
      showError(data.message || firstFieldError(data) || T.network_error);
    } catch (_) {
      showError(T.network_error);
    }
    submitting = false;
    btn.innerHTML = label;
    renderCart();
  });

  function firstFieldError(data) {
    for (const v of Object.values(data || {})) {
      if (Array.isArray(v) && typeof v[0] === "string") return v[0];
    }
    return "";
  }

  function showError(msg) {
    errorEl.textContent = msg;
    errorEl.hidden = false;
    errorEl.scrollIntoView({ block: "nearest", behavior: "smooth" });
  }

  // ---------- Toast ----------
  let toastTimer = null;
  function toast(msg) {
    const el = $("#toast");
    el.textContent = msg;
    el.hidden = false;
    el.classList.add("show");
    clearTimeout(toastTimer);
    toastTimer = setTimeout(() => {
      el.classList.remove("show");
      setTimeout(() => (el.hidden = true), 250);
    }, 1600);
  }

  renderCart();
})();

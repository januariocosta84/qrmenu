/* Analytics page: revenue line chart (this vs previous period) + hover tooltips for bars, cells and segments. */
(function () {
  "use strict";
  const root = document.querySelector(".viz");
  const dataEl = document.getElementById("analytics-data");
  if (!root || !dataEl) return;
  const DATA = JSON.parse(dataEl.textContent);
  const TEXT = window.ANALYTICS_TEXT || { thisPeriod: "This period", previous: "Previous period" };
  const NS = "http://www.w3.org/2000/svg";
  const css = (name) => getComputedStyle(root).getPropertyValue(name).trim();
  const money = (v) => DATA.symbol + Number(v).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const short = (v) => DATA.symbol + (v >= 1000 ? (v / 1000).toLocaleString(undefined, { maximumFractionDigits: 1 }) + "k" : Math.round(v).toLocaleString());
  const fmtDay = (iso) => new Date(iso + "T00:00:00").toLocaleDateString(undefined, { day: "numeric", month: "short" });

  function svgEl(tag, attrs, parent) {
    const e = document.createElementNS(NS, tag);
    Object.entries(attrs).forEach(([k, v]) => e.setAttribute(k, v));
    if (parent) parent.appendChild(e);
    return e;
  }

  // ---- One tooltip element per chart container; content built with textContent only.
  function makeTip(container) {
    const tip = document.createElement("div");
    tip.className = "viz-tip";
    tip.hidden = true;
    container.appendChild(tip);
    return tip;
  }
  function placeTip(tip, container, x, y) {
    tip.hidden = false;
    const w = tip.offsetWidth, h = tip.offsetHeight, cw = container.clientWidth;
    tip.style.left = Math.max(0, Math.min(cw - w, x + 12)) + "px";
    tip.style.top = Math.max(0, y - h - 10) + "px";
  }

  // ---- Simple tooltips for any [data-tip] mark (bars, heat cells, stacked segments).
  const markTip = document.createElement("div");
  markTip.className = "viz-tip";
  markTip.style.position = "fixed";
  markTip.hidden = true;
  document.body.appendChild(markTip);
  function showMarkTip(el, x, y) {
    markTip.textContent = el.dataset.tip;
    markTip.hidden = false;
    const w = markTip.offsetWidth, h = markTip.offsetHeight;
    markTip.style.left = Math.max(4, Math.min(window.innerWidth - w - 4, x + 12)) + "px";
    markTip.style.top = Math.max(4, y - h - 12) + "px";
  }
  root.querySelectorAll("[data-tip]").forEach((el) => {
    el.addEventListener("pointermove", (e) => showMarkTip(el, e.clientX, e.clientY));
    el.addEventListener("pointerleave", () => (markTip.hidden = true));
    el.addEventListener("focus", () => { const r = el.getBoundingClientRect(); showMarkTip(el, r.left + r.width / 2, r.top); });
    el.addEventListener("blur", () => (markTip.hidden = true));
  });

  // ---- Revenue per day: line chart, one y-axis, crosshair + tooltip.
  function niceMax(v) {
    if (v <= 0) return 10;
    const p = Math.pow(10, Math.floor(Math.log10(v)));
    for (const m of [1, 2, 2.5, 5, 10]) if (m * p >= v) return m * p;
    return 10 * p;
  }

  function drawTrend() {
    const box = document.getElementById("trend-chart");
    if (!box) return;
    box.replaceChildren();
    const pts = DATA.trend;
    const W = Math.max(280, box.clientWidth), H = 240, padL = 48, padR = 64, padT = 12, padB = 26;
    const max = niceMax(Math.max(1, ...pts.map((p) => Math.max(p.revenue, p.prev))));
    const n = pts.length;
    const x = (i) => padL + (n === 1 ? (W - padL - padR) / 2 : (i * (W - padL - padR)) / (n - 1));
    const y = (v) => padT + (1 - v / max) * (H - padT - padB);
    const svg = svgEl("svg", { viewBox: `0 0 ${W} ${H}`, width: W, height: H }, box);

    // Recessive grid + y ticks (clean numbers).
    for (let k = 0; k <= 4; k++) {
      const v = (max * k) / 4, yy = y(v);
      svgEl("line", { x1: padL, x2: W - padR, y1: yy, y2: yy, stroke: k ? css("--viz-grid") : css("--viz-axis"), "stroke-width": 1 }, svg);
      const t = svgEl("text", { x: padL - 8, y: yy + 4, "text-anchor": "end" }, svg);
      t.textContent = short(v);
    }
    // X labels: about six evenly spaced days.
    const step = Math.max(1, Math.ceil(n / 6));
    pts.forEach((p, i) => {
      if (i % step && i !== n - 1) return;
      if (i !== n - 1 && n - 1 - i < step / 2) return; // keep the last label clear of its neighbour
      const t = svgEl("text", { x: x(i), y: H - 6, "text-anchor": "middle" }, svg);
      t.textContent = fmtDay(p.day);
    });

    const line = (key) => pts.map((p, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(p[key]).toFixed(1)}`).join("");
    // Previous period: gray, de-emphasised.
    svgEl("path", { d: line("prev"), fill: "none", stroke: css("--viz-prev"), "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
    // This period: wash + 2px line + end dot with surface ring + end label.
    const base = y(0);
    svgEl("path", { d: `${line("revenue")}L${x(n - 1).toFixed(1)},${base}L${x(0).toFixed(1)},${base}Z`, fill: css("--viz-1"), "fill-opacity": 0.1 }, svg);
    svgEl("path", { d: line("revenue"), fill: "none", stroke: css("--viz-1"), "stroke-width": 2, "stroke-linejoin": "round", "stroke-linecap": "round" }, svg);
    const last = pts[n - 1];
    svgEl("circle", { cx: x(n - 1), cy: y(last.revenue), r: 4, fill: css("--viz-1"), stroke: css("--surface") || "#fff", "stroke-width": 2 }, svg);
    const endLabel = svgEl("text", { x: x(n - 1) + 8, y: y(last.revenue) + 4, "text-anchor": "start" }, svg);
    endLabel.textContent = short(last.revenue);
    endLabel.style.fill = css("--ink-2");

    // Crosshair snaps to the nearest day; one tooltip lists both series.
    const cross = svgEl("line", { y1: padT, y2: base, stroke: css("--viz-axis"), "stroke-width": 1, visibility: "hidden" }, svg);
    const dot = svgEl("circle", { r: 4, fill: css("--viz-1"), stroke: css("--surface") || "#fff", "stroke-width": 2, visibility: "hidden" }, svg);
    const tip = makeTip(box);
    const hit = svgEl("rect", { x: padL, y: padT, width: W - padL - padR, height: base - padT, fill: "transparent", tabindex: 0 }, svg);
    let focusIdx = n - 1;

    function row(color, label, value) {
      const r = document.createElement("div");
      r.className = "row";
      const lab = document.createElement("span");
      lab.className = "lab";
      const key = document.createElement("i");
      key.className = "viz-key-line";
      key.style.background = color;
      lab.append(key, document.createTextNode(label));
      const val = document.createElement("strong");
      val.textContent = value;
      r.append(lab, val);
      return r;
    }
    function show(i) {
      const p = pts[i];
      cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
      dot.setAttribute("cx", x(i)); dot.setAttribute("cy", y(p.revenue)); dot.setAttribute("visibility", "visible");
      tip.replaceChildren(
        row(css("--viz-1"), `${TEXT.thisPeriod} · ${fmtDay(p.day)}`, money(p.revenue)),
        row(css("--viz-prev"), `${TEXT.previous} · ${fmtDay(p.prev_day)}`, money(p.prev)),
      );
      const scale = box.clientWidth / W;
      placeTip(tip, box, x(i) * scale, y(Math.max(p.revenue, p.prev)) * scale);
    }
    function hide() { cross.setAttribute("visibility", "hidden"); dot.setAttribute("visibility", "hidden"); tip.hidden = true; }
    hit.addEventListener("pointermove", (e) => {
      const rect = svg.getBoundingClientRect();
      const px = ((e.clientX - rect.left) / rect.width) * W;
      focusIdx = Math.max(0, Math.min(n - 1, Math.round(((px - padL) / (W - padL - padR)) * (n - 1))));
      show(focusIdx);
    });
    hit.addEventListener("pointerleave", hide);
    hit.addEventListener("focus", () => show(focusIdx));
    hit.addEventListener("blur", hide);
    hit.addEventListener("keydown", (e) => {
      if (e.key === "ArrowLeft") { focusIdx = Math.max(0, focusIdx - 1); show(focusIdx); e.preventDefault(); }
      if (e.key === "ArrowRight") { focusIdx = Math.min(n - 1, focusIdx + 1); show(focusIdx); e.preventDefault(); }
    });
  }

  drawTrend();
  let resizeTimer;
  window.addEventListener("resize", () => { clearTimeout(resizeTimer); resizeTimer = setTimeout(drawTrend, 150); });
  window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", drawTrend);
})();

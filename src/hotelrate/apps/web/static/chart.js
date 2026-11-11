/* Forward rate chart (colours come from CSS custom properties): one y-axis, thin 2px lines, a recessive grid, a neutral band for the comp-set range, direct labels
   at the line ends, and a crosshair tooltip (mouse, touch and arrow keys). Gaps in a line are dates without a price. */
(function () {
  "use strict";
  const SVG = "http://www.w3.org/2000/svg";
  const HEIGHT = 340;
  const M = { top: 14, right: 150, bottom: 30, left: 64 };

  function el(name, attrs) {
    const node = document.createElementNS(SVG, name);
    for (const [key, value] of Object.entries(attrs || {})) node.setAttribute(key, value);
    return node;
  }

  function niceTicks(lo, hi, count) {
    if (lo === hi) { lo -= 1; hi += 1; }
    const raw = (hi - lo) / count;
    const power = Math.pow(10, Math.floor(Math.log10(raw)));
    const step = [1, 2, 2.5, 5, 10].map((f) => f * power).find((s) => s >= raw);
    const start = Math.floor(lo / step) * step;
    const ticks = [];
    // Keep adding steps until the top tick is at or above the highest value, so no line leaves the plot.
    for (let v = start; ; v += step) {
      ticks.push(Math.round(v * 100) / 100);
      if (v >= hi) break;
    }
    return ticks;
  }

  function linePath(values, x, y) {
    let d = "";
    let open = false;
    values.forEach((v, i) => {
      if (v == null) { open = false; return; }
      d += (open ? "L" : "M") + x(i).toFixed(1) + "," + y(v).toFixed(1);
      open = true;
    });
    return d;
  }

  function bandPath(lo, hi, x, y) {
    let d = "";
    let run = [];
    const flush = () => {
      if (run.length > 1) {
        d += "M" + run.map((i) => x(i).toFixed(1) + "," + y(hi[i]).toFixed(1)).join("L");
        d += "L" + run.slice().reverse().map((i) => x(i).toFixed(1) + "," + y(lo[i]).toFixed(1)).join("L") + "Z";
      }
      run = [];
    };
    lo.forEach((v, i) => { if (v != null && hi[i] != null) run.push(i); else flush(); });
    flush();
    return d;
  }

  /**
   * cfg: { dates: [iso], series: [{label, values, color, dash}], band: {lo, hi, label} | null,
   *        format: (n) => string, extra: (i) => [{label, value}] }
   */
  function render(container, cfg) {
    container.textContent = "";
    const n = cfg.dates.length;
    if (n < 2) {
      const p = document.createElement("p");
      p.className = "empty";
      p.textContent = "Not enough priced dates to draw.";
      container.appendChild(p);
      return;
    }
    const width = Math.max(container.clientWidth || 800, 320);
    const right = width < 560 ? 16 : M.right;
    const plotW = width - M.left - right;
    const plotH = HEIGHT - M.top - M.bottom;

    const all = [];
    cfg.series.forEach((s) => s.values.forEach((v) => v != null && all.push(v)));
    if (cfg.band) cfg.band.lo.concat(cfg.band.hi).forEach((v) => v != null && all.push(v));
    if (!all.length) { render(container, { ...cfg, dates: [] }); return; }
    const ticks = niceTicks(Math.min(...all), Math.max(...all), 5);
    const yMin = ticks[0];
    const yMax = ticks[ticks.length - 1];
    const x = (i) => M.left + (i / (n - 1)) * plotW;
    const y = (v) => M.top + plotH - ((v - yMin) / (yMax - yMin)) * plotH;

    const svg = el("svg", { viewBox: `0 0 ${width} ${HEIGHT}`, height: HEIGHT, role: "img",
                            "aria-label": cfg.title || "Forward nightly rates" });

    ticks.forEach((t) => {
      svg.appendChild(el("line", { x1: M.left, x2: M.left + plotW, y1: y(t), y2: y(t), stroke: "var(--grid)" }));
      const label = el("text", { x: M.left - 8, y: y(t) + 4, "text-anchor": "end" });
      label.textContent = cfg.format(t);
      svg.appendChild(label);
    });

    // Month labels, skipped where they would collide with the previous one.
    let lastRight = -Infinity;
    cfg.dates.forEach((iso, i) => {
      const day = new Date(iso + "T00:00:00");
      if (day.getDate() !== 1 && i !== 0) return;
      const px = x(i);
      const text = day.toLocaleDateString("en-GB", { month: "short" }) +
        (day.getMonth() === 0 || i === 0 ? " " + day.getFullYear() : "");
      const textWidth = text.length * 6.4;
      const left = i === 0 ? px : px - textWidth / 2;
      if (left < lastRight + 10) return;
      lastRight = left + textWidth;
      const label = el("text", { x: px, y: HEIGHT - 8, "text-anchor": i === 0 ? "start" : "middle" });
      label.textContent = text;
      svg.appendChild(label);
      if (i !== 0) svg.appendChild(el("line", { x1: px, x2: px, y1: M.top + plotH, y2: M.top + plotH + 4,
                                                stroke: "var(--rule-soft)" }));
    });
    svg.appendChild(el("line", { x1: M.left, x2: M.left + plotW, y1: M.top + plotH, y2: M.top + plotH,
                                 stroke: "var(--rule)" }));

    if (cfg.band) svg.appendChild(el("path", { d: bandPath(cfg.band.lo, cfg.band.hi, x, y), fill: "var(--band)" }));
    cfg.series.forEach((s) => {
      svg.appendChild(el("path", { d: linePath(s.values, x, y), fill: "none", stroke: s.color, "stroke-width": 2,
                                   "stroke-dasharray": s.dash || "none", "stroke-linejoin": "round",
                                   "stroke-linecap": "round" }));
    });

    // Direct labels at each line's last priced point, nudged apart; ink text beside a coloured swatch.
    if (right > 40) {
      const labels = cfg.series.map((s) => {
        let i = s.values.length - 1;
        while (i >= 0 && s.values[i] == null) i -= 1;
        return i < 0 ? null : { s, i, ly: y(s.values[i]) };
      }).filter(Boolean).sort((a, b) => a.ly - b.ly);
      for (let k = 1; k < labels.length; k += 1) {
        if (labels[k].ly - labels[k - 1].ly < 15) labels[k].ly = labels[k - 1].ly + 15;
      }
      labels.forEach(({ s, ly }) => {
        const lx = M.left + plotW + 10;
        svg.appendChild(el("line", { x1: lx, x2: lx + 14, y1: ly, y2: ly, stroke: s.color, "stroke-width": 2,
                                     "stroke-dasharray": s.dash || "none" }));
        const text = el("text", { x: lx + 20, y: ly + 4, class: "direct-label" });
        text.textContent = s.short || s.label;
        svg.appendChild(text);
      });
    }

    // Hover layer.
    const cross = el("line", { y1: M.top, y2: M.top + plotH, stroke: "var(--rule)", "stroke-width": 1,
                               visibility: "hidden" });
    svg.appendChild(cross);
    const dots = cfg.series.map((s) => {
      const dot = el("circle", { r: 4, fill: s.color, stroke: "var(--surface)", "stroke-width": 2, visibility: "hidden" });
      svg.appendChild(dot);
      return dot;
    });
    const overlay = el("rect", { x: M.left, y: M.top, width: plotW, height: plotH, class: "overlay", tabindex: 0,
                                 "aria-label": "Rates by date; use the arrow keys to move" });
    svg.appendChild(overlay);
    container.appendChild(svg);

    const tip = document.createElement("div");
    tip.className = "tooltip";
    tip.hidden = true;
    container.appendChild(tip);

    let current = null;
    function show(i) {
      current = Math.max(0, Math.min(n - 1, i));
      const px = x(current);
      cross.setAttribute("x1", px);
      cross.setAttribute("x2", px);
      cross.setAttribute("visibility", "visible");
      cfg.series.forEach((s, k) => {
        const v = s.values[current];
        dots[k].setAttribute("visibility", v == null ? "hidden" : "visible");
        if (v != null) { dots[k].setAttribute("cx", px); dots[k].setAttribute("cy", y(v)); }
      });
      tip.textContent = "";
      const head = document.createElement("div");
      head.className = "date";
      head.textContent = new Date(cfg.dates[current] + "T00:00:00").toLocaleDateString("en-GB",
        { weekday: "short", day: "numeric", month: "short", year: "numeric" });
      tip.appendChild(head);
      const row = (name, value, swatch) => {
        const r = document.createElement("div");
        r.className = "row";
        const left = document.createElement("span");
        left.className = "name";
        if (swatch) left.appendChild(swatch);
        const text = document.createElement("span");
        text.className = "label";
        text.textContent = name;
        left.appendChild(text);
        const right = document.createElement("span");
        right.className = "num";
        right.textContent = value == null ? "no price" : cfg.format(value);
        r.append(left, right);
        tip.appendChild(r);
        return r;
      };
      cfg.series.forEach((s) => row(s.label, s.values[current], swatchFor(s)));
      if (cfg.band && cfg.band.lo[current] != null) {
        const r = row(cfg.band.label, null, bandSwatch());
        r.lastChild.textContent = cfg.format(cfg.band.lo[current]) + " – " + cfg.format(cfg.band.hi[current]);
      }
      const extra = cfg.extra ? cfg.extra(current) : [];
      if (extra.length) {
        const sep = document.createElement("div");
        sep.className = "sep";
        tip.appendChild(sep);
        extra.forEach((item) => row(item.label, item.value));
      }
      tip.hidden = false;
      const box = container.getBoundingClientRect();
      const tipW = tip.offsetWidth;
      const left = px + 14 + tipW > box.width ? px - 14 - tipW : px + 14;
      tip.style.left = Math.max(0, left) + "px";
      tip.style.top = M.top + "px";
    }
    function hide() {
      cross.setAttribute("visibility", "hidden");
      dots.forEach((d) => d.setAttribute("visibility", "hidden"));
      tip.hidden = true;
    }
    function indexAt(clientX) {
      const box = svg.getBoundingClientRect();
      const scale = width / box.width;
      return Math.round((((clientX - box.left) * scale) - M.left) / plotW * (n - 1));
    }
    overlay.addEventListener("pointermove", (e) => show(indexAt(e.clientX)));
    overlay.addEventListener("pointerdown", (e) => show(indexAt(e.clientX)));
    overlay.addEventListener("pointerleave", hide);
    overlay.addEventListener("blur", hide);
    overlay.addEventListener("keydown", (e) => {
      const step = e.shiftKey ? 7 : 1;
      if (e.key === "ArrowRight") { show((current ?? -1) + step); e.preventDefault(); }
      if (e.key === "ArrowLeft") { show((current ?? n) - step); e.preventDefault(); }
      if (e.key === "Escape") hide();
    });
  }

  function swatchFor(series) {
    const svg = el("svg", { width: 16, height: 8, "aria-hidden": "true" });
    svg.appendChild(el("line", { x1: 0, x2: 16, y1: 4, y2: 4, stroke: series.color, "stroke-width": 2,
                                 "stroke-dasharray": series.dash ? "4 2" : "none" }));
    return svg;
  }

  function bandSwatch() {
    const svg = el("svg", { width: 16, height: 10, "aria-hidden": "true" });
    svg.appendChild(el("rect", { x: 0, y: 0, width: 16, height: 10, fill: "var(--band)" }));
    return svg;
  }

  window.RateChart = { render, swatchFor, bandSwatch };
})();

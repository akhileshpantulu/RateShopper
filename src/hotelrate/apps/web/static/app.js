/* Rate Shopper front end: analyst console with map views. Plain DOM, no build step.
   Text from Google always goes in as text nodes, never HTML. */
(function () {
  "use strict";

  const TARGET_COLOR = "#C8811A";
  const COMP_COLOR = "#2390B6";
  const MEDIAN_COLOR = "#C9D3D9";
  const view = document.getElementById("view");
  const state = { config: null, lastSearch: null, poll: null, locateRun: 0, sweep: null };

  // -- helpers -------------------------------------------------------------------------------------------------------

  function h(tag, attrs, ...children) {
    const node = document.createElement(tag);
    for (const [key, value] of Object.entries(attrs || {})) {
      if (value == null || value === false) continue;
      if (key.startsWith("on")) node.addEventListener(key.slice(2), value);
      else if (key === "class") node.className = value;
      else node.setAttribute(key, value === true ? "" : value);
    }
    for (const child of children.flat()) {
      if (child == null || child === false) continue;
      node.appendChild(typeof child === "string" || typeof child === "number" ? document.createTextNode(String(child)) : child);
    }
    return node;
  }

  async function api(path, options) {
    const response = await fetch(path, { headers: { "Content-Type": "application/json" }, ...options });
    if (!response.ok) {
      let message = response.statusText;
      try {
        const body = await response.json();
        message = Array.isArray(body.detail) ? body.detail.map((d) => d.msg).join("; ") : body.detail || message;
      } catch (_) { /* not JSON */ }
      throw new Error(message);
    }
    return response.status === 204 ? null : response.json();
  }

  function money(value, currency, digits = 0) {
    if (value == null) return "--";
    return new Intl.NumberFormat("en-GB", { style: "currency", currency, maximumFractionDigits: digits,
                                            minimumFractionDigits: digits }).format(value);
  }
  const pct = (v) => (v == null ? "--" : (v > 0 ? "+" : "") + (v * 100).toFixed(1) + "%");
  const share = (v) => (v == null ? "--" : (v * 100).toFixed(1) + "%");
  const km = (v) => (v == null ? "--" : v < 1 ? `${Math.round(v * 1000)} m` : `${v.toFixed(2)} km`);
  const fmtDate = (iso) => (iso ? iso.slice(0, 10) : "--");
  const fmtTime = (iso) => (iso ? iso.slice(0, 16).replace("T", " ") + "Z" : "--");
  const signClass = (v) => (v == null ? null : v > 0 ? "pos" : v < 0 ? "neg" : null);

  function haversine(a, b) {
    if (!a || !b || a.latitude == null || b.latitude == null) return null;
    const rad = (d) => (d * Math.PI) / 180;
    const dLat = rad(b.latitude - a.latitude);
    const dLon = rad(b.longitude - a.longitude);
    const x = Math.sin(dLat / 2) ** 2 + Math.cos(rad(a.latitude)) * Math.cos(rad(b.latitude)) * Math.sin(dLon / 2) ** 2;
    return 2 * 6371.0088 * Math.asin(Math.sqrt(x));
  }

  function cell(value, kind, currency) {
    if (value == null || value === "") return "";
    if (typeof value === "string" && kind !== "date") return value;
    switch (kind) {
      case "money": return money(value, currency);
      case "pct": return pct(value);
      case "share": return share(value);
      case "int": return Number(value).toLocaleString("en-GB");
      case "date": return fmtDate(value);
      default: return String(value);
    }
  }

  const numeric = (kind) => ["money", "pct", "share", "int"].includes(kind);

  function dataTable(table, currency, options = {}) {
    if (!table.rows.length) return h("p", { class: "empty" }, options.empty || "NO DATA");
    const head = h("tr", {}, table.columns.map((c) => h("th", { class: numeric(c.kind) ? "num" : null }, c.name)));
    const td = (value, c) => h("td", { class: [numeric(c.kind) ? "num" : "", c.kind === "pct" ? signClass(value) : ""]
      .filter(Boolean).join(" ") || null }, cell(value, c.kind, currency));
    const body = table.rows.map((row) => h("tr", {}, table.columns.map((c, i) => td(row[i], c))));
    const foot = table.total ? h("tfoot", {}, h("tr", {}, table.columns.map((c, i) => td(table.total[i], c)))) : null;
    return h("div", { class: "table-wrap" }, h("table", { class: "data" }, h("thead", {}, head), h("tbody", {}, body), foot));
  }

  function panel(title, options, ...body) {
    const head = h("div", { class: "panel-head" }, h("span", { class: "title" + (options.accent ? " accent" : "") }, title),
      options.tools ? h("div", { class: "controls", style: "margin:0" }, options.tools) : null);
    return h("section", { class: "panel" + (options.class ? " " + options.class : "") }, head,
      h("div", { class: "panel-body" + (options.flush ? " flush" : "") }, body));
  }

  function kpi(label, value, note, valueClass) {
    return h("div", { class: "kpi" }, h("div", { class: "k" }, label),
      h("div", { class: "v" + (valueClass ? " " + valueClass : "") }, value), note ? h("div", { class: "n" }, note) : null);
  }

  function message(text, isError) {
    return h("p", { class: isError ? "error" : "empty", role: isError ? "alert" : null }, text);
  }

  function tag(role) {
    const label = { target: "TGT", comp: "CMP", candidate: "CAND" }[role] || role;
    return h("span", { class: `tag ${role}` }, label);
  }

  function setNav(name) {
    document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === name));
  }

  function stopPolling() {
    if (state.poll) { clearInterval(state.poll); state.poll = null; }
  }

  // -- status bar ----------------------------------------------------------------------------------------------------

  function startStatusBar() {
    const clock = document.getElementById("clock");
    setInterval(() => { clock.textContent = new Date().toISOString().slice(11, 19); }, 1000);
    const refresh = async () => {
      try {
        const status = await api("/api/status");
        const active = status.active_runs.length > 0;
        document.getElementById("collector-state").textContent = active ? `ACTIVE (${status.active_runs.length})` : "IDLE";
        document.getElementById("collector-dot").classList.toggle("active", active);
        document.getElementById("req-count").textContent = status.requests_sent;
        document.getElementById("block-count").textContent = status.blocks_seen;
      } catch (_) {
        document.getElementById("collector-state").textContent = "OFFLINE";
      }
    };
    refresh();
    setInterval(refresh, 5000);
  }

  // -- router --------------------------------------------------------------------------------------------------------

  async function route() {
    stopPolling();
    state.locateRun += 1; // cancels any geolocation still running for the previous page
    const hash = location.hash.replace(/^#/, "") || "/search";
    const [path, query] = hash.split("?");
    const params = new URLSearchParams(query || "");
    const parts = path.split("/").filter(Boolean);
    view.textContent = "";
    try {
      if (!state.config) state.config = await api("/api/config");
      if (parts[0] === "hotel" && parts[1]) return await hotelView(decodeURIComponent(parts[1]), params);
      if (parts[0] === "compsets") return await compsetsView();
      if (parts[0] === "compset" && parts[1]) return await compsetView(Number(parts[1]), params);
      return await searchView(params);
    } catch (error) {
      view.appendChild(message(error.message, true));
    }
  }

  /** Locate candidates one at a time (each is one Google call, cached after); stops when the page changes. */
  async function locateAll(candidates, onLocated, onProgress) {
    const run = state.locateRun;
    const pending = candidates.filter((c) => c.latitude == null);
    let done = candidates.length - pending.length;
    onProgress(done, candidates.length);
    for (const candidate of pending) {
      if (run !== state.locateRun) return;
      try {
        const place = await api(`/api/hotels/${encodeURIComponent(candidate.source_id)}/locate`, { method: "POST" });
        Object.assign(candidate, { latitude: place.latitude, longitude: place.longitude, address: place.address });
        if (run !== state.locateRun) return;
        onLocated(candidate);
      } catch (_) { candidate.locateFailed = true; }
      done += 1;
      onProgress(done, candidates.length);
    }
  }

  // -- search --------------------------------------------------------------------------------------------------------

  async function searchView(params) {
    setNav("search");
    const studies = (await api("/api/studies")).studies;
    const preselect = params.get("study");
    view.append(h("div", { class: "crumb" }, "Search // Google Hotels"), h("h1", {}, "Hotel search"),
      h("p", { class: "meta" }, "Query Google Hotels, geolocate the results, open a hotel's forward rates or add it to a compset."));

    const q = h("input", { class: "wide", name: "q", placeholder: "hotel name or 'hotels near ...'", required: true,
                           value: state.lastSearch?.q || "" });
    const city = h("input", { name: "city", placeholder: "city", value: state.lastSearch?.city || "" });
    const studySelect = h("select", { "aria-label": "Compset" },
      h("option", { value: "" }, studies.length ? "-- compset --" : "no compsets"),
      studies.map((s) => h("option", { value: s.id, selected: String(s.id) === preselect }, s.name)));
    const roleSelect = h("select", { "aria-label": "Role" }, h("option", { value: "comp" }, "as comp"),
      h("option", { value: "target" }, "as target"));
    const results = h("div");
    const progress = h("span", { class: "mono" }, "");
    const mapEl = h("div", { class: "map" });

    const form = h("form", { class: "inline", onsubmit: (e) => { e.preventDefault(); run(); } },
      h("label", { class: "field" }, "Query", q), h("label", { class: "field" }, "City", city),
      h("button", { class: "primary", type: "submit" }, "Search"));
    const addRow = h("div", { class: "controls" }, h("span", { class: "muted mono" }, "ADD TO"), studySelect, roleSelect,
      h("a", { href: "#/compsets", class: "muted mono" }, "new compset"));
    view.appendChild(h("div", { class: "grid search" },
      panel("Query & results", { accent: true }, form, addRow, results),
      panel("Map", { class: "fill", tools: [h("span", { class: "map-legend" },
        h("span", {}, h("span", { class: "swatch-dot candidate" }), "result")), progress], flush: true }, mapEl)));
    const map = RateMap.create(mapEl, state.config);

    async function run() {
      state.locateRun += 1;
      results.textContent = "";
      map.clear();
      results.appendChild(message("QUERYING GOOGLE HOTELS..."));
      try {
        const data = await api(`/api/search?q=${encodeURIComponent(q.value)}&city=${encodeURIComponent(city.value)}`);
        state.lastSearch = { q: q.value, city: city.value, candidates: data.candidates };
        show(data.candidates);
      } catch (error) {
        results.textContent = "";
        results.appendChild(message(error.message, true));
      }
    }

    function point(c) {
      return {
        key: c.source_id, role: "candidate", latitude: c.latitude, longitude: c.longitude, label: c.name,
        popup: () => RateMap.popup(c.name, [["class", c.hotel_class ? `${c.hotel_class}-star` : null],
          ["rating", c.rating ? `${c.rating} (${(c.reviews_count || 0).toLocaleString("en-GB")})` : null],
          ["tonight", c.rate_per_night != null ? money(c.rate_per_night, c.currency || state.config.currency) : null],
          ["address", c.address], ["coords", RateMap.formatCoords(c.latitude, c.longitude)]],
        [h("a", { class: "button small", href: hotelHref(c.source_id, c.name) }, "Open")]),
      };
    }

    function show(candidates) {
      results.textContent = "";
      if (!candidates.length) { results.appendChild(message("NO RESULTS")); return; }
      const coordCells = new Map();
      const rows = candidates.map((c, i) => {
        const note = h("span", { class: "muted" });
        const add = h("button", { type: "button", class: "small", onclick: async (event) => {
          event.stopPropagation();
          if (!studySelect.value) { note.textContent = "choose a compset"; return; }
          add.disabled = true;
          try {
            await api(`/api/studies/${studySelect.value}/hotels`, { method: "POST", body: JSON.stringify({
              source_id: c.source_id, name: c.name, role: roleSelect.value, hotel_class: c.hotel_class,
              rating: c.rating, reviews_count: c.reviews_count, google_url: c.google_url }) });
            note.textContent = "added";
          } catch (error) { note.textContent = error.message; add.disabled = false; }
        } }, "Add");
        const coords = h("div", { class: "coords" }, RateMap.formatCoords(c.latitude, c.longitude) || "locating...");
        coordCells.set(c.source_id, coords);
        return h("tr", { class: "clickable", onclick: () => map.focus(c.source_id) },
          h("td", { class: "idx" }, String(i + 1).padStart(2, "0")),
          h("td", { class: "wrap" }, c.name, coords),
          h("td", {}, c.hotel_class ? `${c.hotel_class}*` : "--"),
          h("td", { class: "num" }, c.rating ?? "--"),
          h("td", { class: "num" }, money(c.rate_per_night, c.currency || state.config.currency)),
          h("td", { class: "actions" },
            h("a", { class: "button small", href: hotelHref(c.source_id, c.name), onclick: (e) => e.stopPropagation() }, "Open"),
            add, note));
      });
      results.appendChild(h("div", { class: "table-wrap" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, ["#", "Hotel", "Class", "Rating", "Tonight", ""].map((t, i) =>
          h("th", { class: i === 3 || i === 4 ? "num" : null }, t)))),
        h("tbody", {}, rows))));
      map.plot(candidates.map(point));
      locateAll(candidates, (c) => {
        coordCells.get(c.source_id).textContent = RateMap.formatCoords(c.latitude, c.longitude) || "no position";
        map.plot([point(c)]);
      }, (done, total) => { progress.textContent = `GEOLOCATED ${done}/${total}`; })
        .then(() => candidates.filter((c) => c.locateFailed).forEach((c) => {
          coordCells.get(c.source_id).textContent = "position unavailable";
        }));
    }

    if (state.lastSearch?.candidates) show(state.lastSearch.candidates);
  }

  function hotelHref(sourceId, name, extra = "") {
    return `#/hotel/${encodeURIComponent(sourceId)}?name=${encodeURIComponent(name || "")}${extra}`;
  }

  // -- one hotel, live ------------------------------------------------------------------------------------------------

  async function hotelView(sourceId, params) {
    setNav("search");
    const name = params.get("name") || "Hotel";
    const nights = Number(params.get("nights") || 1);
    const currency = (params.get("currency") || state.config.currency).toUpperCase();
    view.append(h("div", { class: "crumb" }, h("a", { href: "#/search" }, "Search"), " // Hotel profile"),
      h("h1", {}, name));
    const meta = h("p", { class: "meta" }, h("span", {}, sourceId));
    view.appendChild(meta);
    const nightsSelect = h("select", { "aria-label": "Nights" }, [1, 2, 3, 7].map((n) =>
      h("option", { value: n, selected: n === nights }, `${n}N`)));
    const currencyInput = h("input", { class: "short", value: currency, maxlength: 3, "aria-label": "Currency" });
    view.appendChild(h("form", { class: "inline", onsubmit: (e) => {
      e.preventDefault();
      location.hash = hotelHref(sourceId, name, `&nights=${nightsSelect.value}&currency=${currencyInput.value.toUpperCase()}`);
    } }, h("label", { class: "field" }, "Stay", nightsSelect), h("label", { class: "field" }, "Currency", currencyInput),
      h("button", { type: "submit" }, "Apply")));

    const kpis = h("div", { class: "kpis" }, kpi("Status", "FETCHING", "365-day calendar, one request"));
    const chartBox = h("div", { class: "chart" });
    const mapEl = h("div", { class: "map small" });
    const profile = h("div");
    const forward = h("div");
    view.append(kpis, h("div", { class: "grid two top" },
      panel(`Forward nightly rate // ${nights}N // ${currency}`, { accent: true }, chartBox),
      h("div", {}, panel("Position", { flush: true }, mapEl), panel("Profile", {}, profile))),
      panel("Forward averages", { flush: true }, forward));
    const map = RateMap.create(mapEl, state.config);

    const located = api(`/api/hotels/${encodeURIComponent(sourceId)}/locate`, { method: "POST" }).then((place) => {
      map.plot([{ key: sourceId, role: "target", latitude: place.latitude, longitude: place.longitude, label: name }]);
      map.rings(place.latitude, place.longitude);
      meta.append(h("span", { class: "sep" }, "|"), RateMap.formatCoords(place.latitude, place.longitude) || "no position",
        place.address ? h("span", {}, h("span", { class: "sep" }, "|"), place.address) : null);
      profile.append(dataTable({ columns: [{ name: "Field" }, { name: "Value" }], total: null, rows: [
        ["Class", place.hotel_class ? `${place.hotel_class}-star` : "--"],
        ["Rating", place.rating ? `${place.rating} (${(place.reviews_count || 0).toLocaleString("en-GB")} reviews)` : "--"],
        ["Address", place.address || "--"],
        ["Coordinates", RateMap.formatCoords(place.latitude, place.longitude) || "--"],
        ["Source", place.cached ? "stored location" : "located now"],
      ] }, currency));
      if (place.google_url) profile.append(h("p", {}, h("a", { href: place.google_url, target: "_blank", rel: "noopener" },
        "Open in Google Hotels")));
    }).catch((error) => profile.append(message(`Position unavailable: ${error.message}`, true)));

    const data = await api(`/api/hotels/${encodeURIComponent(sourceId)}/calendar?nights=${nights}&currency=${currency}`);
    const pick = (days) => data.forward.find((row) => row.days === days) || {};
    kpis.textContent = "";
    kpis.append(
      kpi("Next 7 days", money(pick(7).average, data.currency), `${pick(7).dates || 0} dates`),
      kpi("Next 30 days", money(pick(30).average, data.currency), `${pick(30).dates || 0} dates`),
      kpi("Next 90 days", money(pick(90).average, data.currency), `${pick(90).dates || 0} dates`),
      kpi("Next 365 days", money(pick(365).average, data.currency), `${pick(365).dates || 0} dates`),
      kpi("Horizon", fmtDate(data.horizon), `${data.priced_days} priced`),
      kpi("No price", String(data.no_price_days), "possible sell-outs"));
    const draw = () => RateChart.render(chartBox, {
      title: `${name} forward nightly rate`, dates: data.series.dates,
      series: [{ label: name, short: "Rate", values: data.series.rates, color: TARGET_COLOR }],
      format: (v) => money(v, data.currency),
    });
    draw();
    onResize(() => { draw(); map.invalidate(); });
    forward.appendChild(dataTable({ columns: [{ name: "Window" }, { name: "Avg nightly rate", kind: "money" },
                                              { name: "Priced dates", kind: "int" }],
      rows: data.forward.map((row) => [`Next ${row.days} days`, row.average, row.dates]), total: null }, data.currency));
    await located;
  }

  // -- compsets --------------------------------------------------------------------------------------------------------

  async function compsetsView() {
    setNav("compsets");
    const { studies } = await api("/api/studies");
    view.append(h("div", { class: "crumb" }, "Compsets"), h("h1", {}, "Compsets"),
      h("p", { class: "meta" }, "Target hotel plus competitors, collected and reported together."));
    const list = studies.length ? h("div", { class: "table-wrap" }, h("table", { class: "data" },
      h("thead", {}, h("tr", {}, ["Compset", "City", "CCY", "Hotels", "Last collection", ""].map((t, i) =>
        h("th", { class: i === 3 ? "num" : null }, t)))),
      h("tbody", {}, studies.map((s) => h("tr", { class: "clickable", onclick: () => { location.hash = `#/compset/${s.id}`; } },
        h("td", {}, s.name), h("td", {}, s.city), h("td", {}, s.currency), h("td", { class: "num" }, s.hotels),
        h("td", {}, s.last_run ? h("span", {}, fmtTime(s.last_run.started_at), " ", runStatus(s.last_run.status)) : "never"),
        h("td", { class: "actions" }, h("a", { class: "button small", href: `#/compset/${s.id}` }, "Open"))))))) :
      message("NO COMPSETS");

    const name = h("input", { class: "wide", required: true, placeholder: "e.g. London Strand" });
    const city = h("input", { required: true, placeholder: "city" });
    const currency = h("input", { class: "short", required: true, maxlength: 3, value: state.config.currency });
    const nights = h("select", {}, h("option", { value: "1,2" }, "1N + 2N"), h("option", { value: "1" }, "1N"));
    const months = h("select", {}, h("option", { value: "12" }, "12 sample weeks"),
      h("option", { value: "3" }, "3 sample weeks"), h("option", { value: "0" }, "events only"));
    const note = h("p", { class: "error" });
    const form = h("form", { class: "inline", onsubmit: async (e) => {
      e.preventDefault();
      try {
        const study = await api("/api/studies", { method: "POST", body: JSON.stringify({
          name: name.value, city: city.value, currency: currency.value.toUpperCase(),
          calendar_nights: nights.value.split(",").map(Number), offers_months: Number(months.value) }) });
        location.hash = `#/compset/${study.id}`;
      } catch (error) { note.textContent = error.message; }
    } }, h("label", { class: "field" }, "Name", name), h("label", { class: "field" }, "City", city),
      h("label", { class: "field" }, "CCY", currency), h("label", { class: "field" }, "Calendar", nights),
      h("label", { class: "field" }, "Channel offers", months), h("button", { class: "primary", type: "submit" }, "Create"));
    view.append(panel("Compsets", { accent: true, flush: studies.length > 0 }, list), panel("New compset", {}, form, note));
  }

  function runStatus(status) {
    const cls = { succeeded: "status-ok", partial: "status-warn", failed: "status-bad", running: "status-warn" }[status];
    return h("span", { class: cls || null }, (status || "").toUpperCase());
  }

  async function compsetView(id, params) {
    setNav("compsets");
    const nights = params.get("nights");
    const [study, dash] = await Promise.all([
      api(`/api/studies/${id}`), api(`/api/studies/${id}/dashboard${nights ? `?nights=${nights}` : ""}`)]);
    const currency = dash.currency;
    const target = study.hotels.find((hotel) => hotel.role === "target");
    const metrics = new Map(dash.map.map((m) => [m.id, m]));
    const latest = study.runs[0];

    view.append(h("div", { class: "crumb" }, h("a", { href: "#/compsets" }, "Compsets"), ` // ${study.city}`),
      h("h1", {}, study.name),
      h("p", { class: "meta" }, `${study.city}`, h("span", { class: "sep" }, "|"), study.currency,
        h("span", { class: "sep" }, "|"), `${study.adults} adults`, h("span", { class: "sep" }, "|"),
        `calendar ${study.calendar_days}d x ${study.calendar_nights.map((n) => n + "N").join("/")}`,
        h("span", { class: "sep" }, "|"),
        `offers ${study.offers_months ? study.offers_months + " sample week" + (study.offers_months === 1 ? "" : "s") : "events only"}` +
        " + event dates", dash.as_of ? h("span", {}, h("span", { class: "sep" }, "|"), `snapshot ${dash.as_of}`) : null));

    // KPI strip
    const rows = dash.forward.rows;
    const byWindow = (days) => rows.find((row) => row[0] === `Next ${days} days`) || [];
    const w30 = byWindow(30);
    const w365 = byWindow(365);
    const located = study.hotels.filter((hotel) => hotel.latitude != null).length;
    view.appendChild(h("div", { class: "kpis" },
      kpi("Target 30d", money(w30[2], currency), target ? target.name : "no target"),
      kpi("Comp median 30d", money(w30[3], currency), `${w30[5] || 0} matched dates`),
      kpi("Delta 30d", pct(w30[4]), "target vs median", signClass(w30[4])),
      kpi("Delta 365d", pct(w365[4]), `${w365[5] || 0} matched dates`, signClass(w365[4])),
      kpi("Hotels", String(study.hotels.length), `${located} geolocated`),
      kpi("Last collection", latest ? fmtTime(latest.started_at).slice(5) : "never",
          latest ? `${latest.started_at.slice(0, 4)} | ${latest.status.toUpperCase()}` : "")));

    // Map + roster
    const mapEl = h("div", { class: "map" });
    const sweepBox = h("div");
    const sweepButton = h("button", { type: "button", class: "small", disabled: !target, onclick: () => sweep() },
      "Sweep nearby");
    const sweepStatus = h("span", { class: "mono muted" });
    const roster = rosterTable(id, study, metrics, currency, (key) => map.focus(key));
    view.appendChild(h("div", { class: "grid two" },
      panel("Area map", { accent: true, flush: true, class: "fill", tools: [h("span", { class: "map-legend" },
        h("span", {}, h("span", { class: "swatch-dot target" }), "target"),
        h("span", {}, h("span", { class: "swatch-dot comp" }), "comp"),
        h("span", {}, h("span", { class: "swatch-dot candidate" }), "candidate")), sweepStatus, sweepButton] }, mapEl),
      h("div", {}, panel("Roster", { flush: true, tools: [h("a", { class: "button small", href: `#/search?study=${id}` }, "Search")] },
        roster), sweepBox)));
    const map = RateMap.create(mapEl, state.config);
    map.plot(study.hotels.map((hotel) => {
      const m = metrics.get(hotel.id) || {};
      return {
        key: hotel.source_id, role: hotel.role, latitude: hotel.latitude, longitude: hotel.longitude, label: hotel.name,
        popup: () => RateMap.popup(hotel.name, [
          ["role", hotel.role.toUpperCase()], ["distance", hotel.role === "target" ? null : km(hotel.distance_km)],
          ["next 30d", m.next_30 != null ? money(m.next_30, currency) : null],
          ["next 365d", m.next_365 != null ? money(m.next_365, currency) : null],
          ["vs target 30d", hotel.role === "target" ? null : pct(m.vs_target_30)],
          ["address", hotel.address], ["coords", RateMap.formatCoords(hotel.latitude, hotel.longitude)]],
        [h("a", { class: "button small", href: hotelHref(hotel.source_id, hotel.name, `&currency=${currency}`) }, "Profile")]),
      };
    }));
    if (target) map.rings(target.latitude, target.longitude);

    async function sweep() {
      sweepButton.disabled = true;
      sweepStatus.textContent = "QUERYING...";
      try {
        const data = await api(`/api/studies/${id}/nearby`);
        state.sweep = { studyId: id, query: data.query, candidates: data.candidates };
        showSweep();
      } catch (error) { sweepStatus.textContent = error.message; }
      sweepButton.disabled = false;
    }

    function showSweep() {
      if (!state.sweep || state.sweep.studyId !== id) return;
      const inStudy = new Set(study.hotels.map((hotel) => hotel.source_id));
      const candidates = state.sweep.candidates.filter((c) => !inStudy.has(c.source_id));
      const distanceCells = new Map();
      const point = (c) => ({ key: c.source_id, role: "candidate", latitude: c.latitude, longitude: c.longitude,
        label: c.name, popup: () => RateMap.popup(c.name, [["distance", km(c.distance_km)],
          ["class", c.hotel_class ? `${c.hotel_class}-star` : null], ["rating", c.rating],
          ["tonight", c.rate_per_night != null ? money(c.rate_per_night, c.currency || currency) : null],
          ["address", c.address]], [addButton(c, "comp")]) });
      const addButton = (c, role) => h("button", { type: "button", class: "small", onclick: async (event) => {
        event.stopPropagation();
        event.target.disabled = true;
        try {
          await api(`/api/studies/${id}/hotels`, { method: "POST", body: JSON.stringify({ source_id: c.source_id,
            name: c.name, role, hotel_class: c.hotel_class, rating: c.rating, reviews_count: c.reviews_count,
            google_url: c.google_url }) });
          route();
        } catch (error) { sweepStatus.textContent = error.message; event.target.disabled = false; }
      } }, "Add comp");
      sweepBox.textContent = "";
      const table = h("div", { class: "table-wrap" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, ["", "Hotel", "Dist", "Class", "Rating", "Tonight", ""].map((t, i) =>
          h("th", { class: i >= 2 && i <= 5 && i !== 3 ? "num" : null }, t)))),
        h("tbody", {}, candidates.map((c) => {
          const dist = h("td", { class: "num" }, km(c.distance_km ?? haversine(target, c)));
          distanceCells.set(c.source_id, dist);
          return h("tr", { class: "clickable", onclick: () => map.focus(c.source_id) }, h("td", {}, tag("candidate")),
            h("td", { class: "wrap" }, c.name), dist, h("td", {}, c.hotel_class ? `${c.hotel_class}*` : "--"),
            h("td", { class: "num" }, c.rating ?? "--"),
            h("td", { class: "num" }, money(c.rate_per_night, c.currency || currency)),
            h("td", { class: "actions" }, addButton(c, "comp")));
        }))));
      sweepBox.appendChild(panel(`Candidates // ${state.sweep.query}`, { flush: true }, candidates.length ? table :
        message("NO NEW CANDIDATES")));
      map.plot(candidates.map(point), { fit: false });
      if (candidates.length) map.fit();
      locateAll(candidates, (c) => {
        c.distance_km = haversine(target, c);
        distanceCells.get(c.source_id).textContent = km(c.distance_km);
        map.plot([point(c)], { fit: false });
      }, (done, total) => { sweepStatus.textContent = `GEOLOCATED ${done}/${total}`; }).then(() => map.fit());
    }
    showSweep();

    // Chart
    if (dash.series.dates.length) chartPanel(id, study, dash);
    else view.appendChild(panel("Forward nightly rate", {}, message(target ? "NO RATES COLLECTED YET: RUN A COLLECTION"
      : "ADD A TARGET AND COMPS, THEN COLLECT")));

    if (dash.forward.rows.length) {
      view.appendChild(h("div", { class: "grid half" },
        panel("Target vs comp set median", { flush: true }, dataTable(dash.forward, currency),
          h("p", { class: "empty", style: "padding:0 10px" }, "Dates where the target and at least one comp are priced.")),
        panel("Forward averages by hotel", { flush: true }, dataTable(dash.forward_hotels, currency),
          h("p", { class: "empty", style: "padding:0 10px" }, "Each hotel over its own priced dates; median row = daily comp median."))));
    }

    view.appendChild(h("div", { class: "grid half" }, collectPanel(id, study), eventsPanel(study)));

    const reportTables = dash.report.filter((table) => !["Rate Shop Overview"].includes(table.title) &&
      !table.title.startsWith("Forward Average Rate") && !table.title.startsWith("Target vs Comp Set, Forward"));
    view.appendChild(panel("Report", { accent: true, tools: [
      h("a", { class: "button primary small", href: `/api/studies/${id}/report.xlsx` }, "Download Excel")] },
      h("span", { class: "muted mono" }, "Rate position, events, parity, room ladder, sell-out signals, changes.")));
    view.appendChild(h("div", { class: "report-grid" },
      reportTables.map((table) => panel(table.title, { flush: true }, dataTable(table, currency)))));
  }

  function rosterTable(id, study, metrics, currency, focus) {
    if (!study.hotels.length) return message("NO HOTELS: USE SEARCH OR SWEEP NEARBY");
    return h("div", { class: "table-wrap" }, h("table", { class: "data" },
      h("thead", {}, h("tr", {}, ["", "Hotel", "Dist", "30d avg", "vs tgt", ""].map((t, i) =>
        h("th", { class: i >= 2 && i <= 4 ? "num" : null }, t)))),
      h("tbody", {}, study.hotels.map((hotel) => {
        const m = metrics.get(hotel.id) || {};
        return h("tr", { class: "clickable", onclick: () => focus(hotel.source_id) },
          h("td", {}, tag(hotel.role)),
          h("td", { class: "wrap" }, hotel.name,
            h("div", { class: "coords" }, RateMap.formatCoords(hotel.latitude, hotel.longitude) || "no position")),
          h("td", { class: "num" }, hotel.role === "target" ? "--" : km(hotel.distance_km)),
          h("td", { class: "num" }, money(m.next_30, currency)),
          h("td", { class: "num " + (signClass(m.vs_target_30) || "") }, hotel.role === "target" ? "--" : pct(m.vs_target_30)),
          h("td", { class: "actions" },
            h("a", { class: "button small", href: hotelHref(hotel.source_id, hotel.name, `&currency=${study.currency}`),
                     onclick: (e) => e.stopPropagation() }, "Profile"),
            h("button", { type: "button", class: "small", onclick: async (event) => {
              event.stopPropagation();
              if (!confirm(`Remove ${hotel.name} from ${study.name}? Collected rates are kept.`)) return;
              await api(`/api/studies/${id}/hotels/${hotel.id}`, { method: "DELETE" });
              route();
            } }, "x")));
      }))));
  }

  function chartPanel(id, study, dash) {
    const currency = dash.currency;
    const s = dash.series;
    const ranges = [30, 90, 180, 365];
    let range = 90;
    const chart = h("div", { class: "chart" });
    const buttons = ranges.map((days) => h("button", { type: "button", class: "small", "aria-pressed": String(days === range),
      onclick: () => { range = days; buttons.forEach((b, i) => b.setAttribute("aria-pressed", String(ranges[i] === days))); draw(); } },
      days === 365 ? "1Y" : `${days}D`));
    const nightsToggle = dash.nights_options.length > 1 ? h("div", { class: "segmented", role: "group", "aria-label": "Stay" },
      dash.nights_options.map((n) => h("button", { type: "button", class: "small", "aria-pressed": String(n === dash.nights),
        onclick: () => { location.hash = `#/compset/${id}?nights=${n}`; } }, `${n}N`))) : null;
    const legend = h("div", { class: "legend" },
      s.target ? h("span", { class: "legend-item" }, RateChart.swatchFor({ color: TARGET_COLOR }), s.target.name) : null,
      s.comps.length ? h("span", { class: "legend-item" }, RateChart.swatchFor({ color: MEDIAN_COLOR, dash: "5 3" }), "Comp median") : null,
      s.comps.length > 1 ? h("span", { class: "legend-item" }, RateChart.bandSwatch(), "Comp range") : null);
    view.appendChild(panel(`Forward nightly rate // ${dash.nights}N // ${currency}`, { accent: true, tools: [nightsToggle,
      h("div", { class: "segmented", role: "group", "aria-label": "Range" }, buttons)] }, legend, chart));
    const draw = () => {
      const n = Math.min(range, s.dates.length);
      const cut = (values) => values.slice(0, n);
      const series = [];
      if (s.target) series.push({ label: s.target.name, short: "Target", values: cut(s.target.rates), color: TARGET_COLOR });
      if (s.comps.length) series.push({ label: "Comp set median", short: "Comp median", values: cut(s.median),
                                         color: MEDIAN_COLOR, dash: "5 3" });
      RateChart.render(chart, {
        title: "Forward nightly rates, target against comp set", dates: cut(s.dates), series,
        band: s.comps.length > 1 ? { lo: cut(s.low), hi: cut(s.high), label: "Comp set range" } : null,
        format: (v) => money(v, currency),
        extra: (i) => s.comps.map((c) => ({ label: c.name, value: c.rates[i] })).sort((a, b) => (b.value ?? -1) - (a.value ?? -1)),
      });
    };
    draw();
    onResize(draw);
  }

  function collectPanel(id, study) {
    const seconds = state.config.seconds_per_request;
    const hotels = study.hotels.length;
    const calendarRequests = hotels * study.calendar_nights.length;
    const offerDates = Math.round(study.offers_months * 7 * 0.85) + study.events.length;
    const minutes = (requests) => Math.max(1, Math.round((requests * seconds) / 60));
    const live = h("div");
    const start = (calendarOnly) => async () => {
      try {
        const { run_id: runId } = await api(`/api/studies/${id}/runs`, { method: "POST",
          body: JSON.stringify({ calendar_only: calendarOnly }) });
        watch(runId);
      } catch (error) { live.textContent = ""; live.appendChild(message(error.message, true)); }
    };
    const body = [
      h("div", { class: "controls" },
        h("button", { type: "button", class: "primary", disabled: !hotels, onclick: start(true) }, "Calendars"),
        h("span", { class: "mono muted" }, `~${minutes(calendarRequests)} min`),
        h("button", { type: "button", disabled: !hotels, onclick: start(false) }, "Full collection"),
        h("span", { class: "mono muted" }, `~${offerDates} offer dates/hotel, ~${minutes(calendarRequests + hotels * offerDates)} min`)),
      h("p", { class: "empty" }, "Rates collected today are reused; re-running only fetches what is missing or failed."),
      live,
    ];
    if (study.runs[0]) {
      renderRun(live, study.runs[0]);
      if (study.runs[0].active) setTimeout(() => watch(study.runs[0].id), 0);
    }
    function watch(runId) {
      stopPolling();
      const tick = async () => {
        try {
          const run = await api(`/api/runs/${runId}`);
          renderRun(live, run);
          if (!run.active) { stopPolling(); if (run.status !== "running") setTimeout(route, 600); }
        } catch (error) { stopPolling(); live.appendChild(message(error.message, true)); }
      };
      tick();
      state.poll = setInterval(tick, 3000);
    }
    return panel("Collection", { accent: true }, body);
  }

  function renderRun(box, run) {
    box.textContent = "";
    const counts = Object.entries(run.counts || {}).filter(([, c]) => Object.keys(c).length)
      .map(([task, c]) => `${task}: ` + Object.entries(c).map(([k, v]) => `${v} ${k}`).join(", ")).join(" | ");
    box.appendChild(h("div", { class: "run-line" }, `RUN #${run.id} `, run.active ? h("span", { class: "status-warn" }, "ACTIVE")
      : runStatus(run.status), ` ${fmtTime(run.started_at)}`, counts ? ` | ${counts}` : "",
      run.requests_sent ? ` | ${run.requests_sent} req` : ""));
    if (run.status === "running" && !run.active) {
      box.appendChild(h("p", { class: "empty" }, "Not running in this app (interrupted or elsewhere). Start again to resume."));
    }
    if (run.error) box.appendChild(h("p", { class: "error" }, run.error));
    if (run.messages && run.messages.length) box.appendChild(h("div", { class: "feed" }, run.messages.join("\n")));
  }

  function eventsPanel(study) {
    const list = study.events.length ? dataTable({ columns: [{ name: "Event" }, { name: "From", kind: "date" },
      { name: "To", kind: "date" }], rows: study.events.map((e) => [e.name, e.event_date, e.end_date || e.event_date]),
      total: null }, study.currency) : message("NO EVENTS: EVENT DATES GET CHANNEL OFFERS AND AN UPLIFT ANALYSIS");
    const name = h("input", { required: true, placeholder: "event" });
    const from = h("input", { type: "date", required: true });
    const to = h("input", { type: "date" });
    const note = h("span", { class: "error" });
    const form = h("form", { class: "inline", onsubmit: async (e) => {
      e.preventDefault();
      try {
        await api("/api/events", { method: "POST", body: JSON.stringify({ city: study.city, name: name.value,
          event_date: from.value, end_date: to.value || null, country: study.country }) });
        route();
      } catch (error) { note.textContent = error.message; }
    } }, h("label", { class: "field" }, "Event", name), h("label", { class: "field" }, "From", from),
      h("label", { class: "field" }, "To", to), h("button", { type: "submit" }, "Add"), note);
    return panel(`Events // ${study.city}`, {}, list, form);
  }

  // -- resize --------------------------------------------------------------------------------------------------------

  let resizeHandler = null;
  function onResize(draw) {
    if (resizeHandler) window.removeEventListener("resize", resizeHandler);
    let timer = null;
    resizeHandler = () => { clearTimeout(timer); timer = setTimeout(draw, 150); };
    window.addEventListener("resize", resizeHandler);
  }

  window.addEventListener("hashchange", route);
  startStatusBar();
  route();
})();

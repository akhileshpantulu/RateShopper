/* Rate Shopper front end. Plain DOM, no build step. Text from Google always goes in as text nodes, never HTML. */
(function () {
  "use strict";

  const TARGET_COLOR = "#9F7044"; // bronze: the emphasised series
  const MEDIAN_COLOR = "#030303"; // ink, dashed: the reference series
  const view = document.getElementById("view");
  const state = { config: null, lastSearch: null, poll: null };

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
    if (value == null) return "–";
    return new Intl.NumberFormat("en-GB", { style: "currency", currency, maximumFractionDigits: digits,
                                            minimumFractionDigits: digits }).format(value);
  }
  const pct = (v) => (v == null ? "–" : (v > 0 ? "+" : "") + (v * 100).toFixed(1) + "%");
  const share = (v) => (v == null ? "–" : (v * 100).toFixed(1) + "%");
  const fmtDate = (iso) => (iso ? new Date(iso.slice(0, 10) + "T00:00:00").toLocaleDateString("en-GB",
    { day: "numeric", month: "short", year: "numeric" }) : "–");
  const fmtTime = (iso) => (iso ? new Date(iso).toLocaleString("en-GB", { day: "numeric", month: "short",
    hour: "2-digit", minute: "2-digit" }) : "–");

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

  function dataTable(table, currency, options = {}) {
    if (!table.rows.length) return h("p", { class: "empty" }, options.empty || "No data yet.");
    const numeric = (kind) => ["money", "pct", "share", "int"].includes(kind);
    const head = h("tr", {}, table.columns.map((c) => h("th", { class: numeric(c.kind) ? "num" : null }, c.name)));
    const body = table.rows.map((row) => h("tr", { class: row[1] === "Target" ? "target" : null },
      table.columns.map((c, i) => h("td", { class: numeric(c.kind) ? "num" : null }, cell(row[i], c.kind, currency)))));
    const foot = table.total ? h("tfoot", {}, h("tr", {}, table.columns.map((c, i) =>
      h("td", { class: numeric(c.kind) ? "num" : null }, cell(table.total[i], c.kind, currency))))) : null;
    return h("div", { class: "table-wrap" }, h("table", { class: "data" }, h("thead", {}, head), h("tbody", {}, body), foot));
  }

  function status(text, isError) {
    return h("p", { class: isError ? "error" : "muted", role: isError ? "alert" : null }, text);
  }

  function setNav(name) {
    document.querySelectorAll("[data-nav]").forEach((a) => a.classList.toggle("active", a.dataset.nav === name));
  }

  function stopPolling() {
    if (state.poll) { clearInterval(state.poll); state.poll = null; }
  }

  // -- router --------------------------------------------------------------------------------------------------------

  async function route() {
    stopPolling();
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
      view.appendChild(status(error.message, true));
    }
  }

  // -- hotel search --------------------------------------------------------------------------------------------------

  async function searchView(params) {
    setNav("search");
    const studies = (await api("/api/studies")).studies;
    const preselect = params.get("study");
    view.append(
      h("p", { class: "kicker" }, "Hotels"),
      h("h1", {}, "Find a hotel"),
      h("p", { class: "lede" }, "Search Google Hotels by name, then view its forward rates or add it to a compset."),
    );
    const q = h("input", { class: "wide", name: "q", placeholder: "Hotel name", required: true, autofocus: true,
                           value: state.lastSearch?.q || "" });
    const city = h("input", { name: "city", placeholder: "City (optional)", value: state.lastSearch?.city || "" });
    const studySelect = h("select", { "aria-label": "Compset" },
      h("option", { value: "" }, studies.length ? "Choose a compset" : "No compsets yet"),
      studies.map((s) => h("option", { value: s.id, selected: String(s.id) === preselect }, s.name)));
    const roleSelect = h("select", { "aria-label": "Role" }, h("option", { value: "comp" }, "as a comp"),
      h("option", { value: "target" }, "as the target"));
    const results = h("div");
    const form = h("form", { class: "inline", onsubmit: (e) => { e.preventDefault(); run(); } },
      h("label", { class: "field" }, "Hotel", q), h("label", { class: "field" }, "City", city),
      h("button", { class: "primary", type: "submit" }, "Search"));
    view.append(form,
      h("div", { class: "controls" }, h("span", { class: "muted" }, "Add results to"), studySelect, roleSelect,
        h("a", { href: "#/compsets", class: "muted" }, "New compset")),
      results);

    async function run() {
      results.textContent = "";
      results.appendChild(status("Searching Google Hotels…"));
      try {
        const data = await api(`/api/search?q=${encodeURIComponent(q.value)}&city=${encodeURIComponent(city.value)}`);
        state.lastSearch = { q: q.value, city: city.value, candidates: data.candidates };
        show(data.candidates);
      } catch (error) {
        results.textContent = "";
        results.appendChild(status(error.message, true));
      }
    }

    function show(candidates) {
      results.textContent = "";
      if (!candidates.length) { results.appendChild(h("p", { class: "empty" }, "No hotels found.")); return; }
      const rows = candidates.map((c) => {
        const note = h("span", { class: "muted" });
        const add = h("button", { type: "button", onclick: async () => {
          if (!studySelect.value) { note.textContent = "Choose a compset first"; return; }
          add.disabled = true;
          try {
            await api(`/api/studies/${studySelect.value}/hotels`, { method: "POST", body: JSON.stringify({
              source_id: c.source_id, name: c.name, role: roleSelect.value, hotel_class: c.hotel_class,
              rating: c.rating, reviews_count: c.reviews_count, google_url: c.google_url }) });
            note.textContent = "Added";
          } catch (error) { note.textContent = error.message; add.disabled = false; }
        } }, "Add");
        return h("tr", {},
          h("td", { class: "wrap" }, c.name, h("div", { class: "source-id" }, c.property_type || "")),
          h("td", {}, c.hotel_class ? `${c.hotel_class}-star` : "–"),
          h("td", { class: "num" }, c.rating ?? "–"),
          h("td", { class: "num" }, c.reviews_count != null ? c.reviews_count.toLocaleString("en-GB") : "–"),
          h("td", { class: "num" }, money(c.rate_per_night, c.currency || state.config.currency)),
          h("td", { class: "actions" },
            h("a", { class: "button", href: `#/hotel/${encodeURIComponent(c.source_id)}?name=${encodeURIComponent(c.name || "")}` },
              "View rates"), add, note));
      });
      results.appendChild(h("div", { class: "table-wrap" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, ["Hotel", "Class", "Rating", "Reviews", "Tonight from", ""].map((t, i) =>
          h("th", { class: i >= 2 && i <= 4 ? "num" : null }, t)))),
        h("tbody", {}, rows))));
    }

    if (state.lastSearch?.candidates) show(state.lastSearch.candidates);
  }

  // -- one hotel, live ------------------------------------------------------------------------------------------------

  async function hotelView(sourceId, params) {
    setNav("search");
    const name = params.get("name") || "Hotel";
    const nights = Number(params.get("nights") || 1);
    const currency = (params.get("currency") || state.config.currency).toUpperCase();
    view.append(h("p", { class: "kicker" }, "Hotel"), h("h1", {}, name));
    const nightsSelect = h("select", { "aria-label": "Nights" }, [1, 2, 3, 7].map((n) =>
      h("option", { value: n, selected: n === nights }, n === 1 ? "1 night" : `${n} nights`)));
    const currencyInput = h("input", { class: "short", value: currency, maxlength: 3, "aria-label": "Currency" });
    view.appendChild(h("form", { class: "inline", onsubmit: (e) => {
      e.preventDefault();
      location.hash = `#/hotel/${encodeURIComponent(sourceId)}?name=${encodeURIComponent(name)}` +
        `&nights=${nightsSelect.value}&currency=${currencyInput.value.toUpperCase()}`;
    } }, h("label", { class: "field" }, "Stay", nightsSelect), h("label", { class: "field" }, "Currency", currencyInput),
      h("button", { type: "submit" }, "Update"), h("a", { href: "#/search", class: "muted" }, "Back to search")));
    const body = h("div", {}, status("Fetching a year of rates from Google Hotels…"));
    view.appendChild(body);
    const data = await api(`/api/hotels/${encodeURIComponent(sourceId)}/calendar?nights=${nights}&currency=${currency}`);
    body.textContent = "";
    body.appendChild(h("p", { class: "meta" },
      `${data.currency} · ${nights === 1 ? "1 night" : nights + " nights"} · ${data.priced_days} priced check-in dates ` +
      `to ${fmtDate(data.horizon)} · ${data.no_price_days} without a price · live, ${fmtDate(data.start)}`));
    const pick = (days) => data.forward.find((row) => row.days === days) || {};
    body.appendChild(h("div", { class: "figures" }, [30, 90, 365].map((days) => h("div", { class: "figure" },
      h("div", { class: "label" }, `Next ${days} days`),
      h("div", { class: "value" }, money(pick(days).average, data.currency)),
      h("div", { class: "note" }, `average nightly rate, ${pick(days).dates || 0} dates`)))));
    body.appendChild(h("h2", {}, "Forward nightly rate"));
    const chart = h("div", { class: "chart" });
    body.appendChild(chart);
    const draw = () => RateChart.render(chart, {
      title: `${name} forward nightly rate`, dates: data.series.dates,
      series: [{ label: name, short: "Rate", values: data.series.rates, color: TARGET_COLOR }],
      format: (v) => money(v, data.currency),
    });
    draw();
    onResize(draw);
    body.appendChild(h("h2", {}, "Forward averages"));
    body.appendChild(dataTable({
      title: "", columns: [{ name: "Window" }, { name: "Average nightly rate", kind: "money" },
                           { name: "Priced dates", kind: "int" }],
      rows: data.forward.map((row) => [`Next ${row.days} days`, row.average, row.dates]), total: null,
    }, data.currency));
  }

  // -- compsets --------------------------------------------------------------------------------------------------------

  async function compsetsView() {
    setNav("compsets");
    const { studies } = await api("/api/studies");
    view.append(h("p", { class: "kicker" }, "Compsets"), h("h1", {}, "Compsets"),
      h("p", { class: "lede" }, "A compset is a target hotel and its competitors, collected and reported together."));
    if (studies.length) {
      view.appendChild(h("div", { class: "table-wrap" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, ["Compset", "City", "Currency", "Hotels", "Last collection", ""].map((t) => h("th", {}, t)))),
        h("tbody", {}, studies.map((s) => h("tr", {},
          h("td", {}, h("a", { href: `#/compset/${s.id}` }, s.name)), h("td", {}, s.city), h("td", {}, s.currency),
          h("td", { class: "num" }, s.hotels),
          h("td", {}, s.last_run ? `${fmtTime(s.last_run.started_at)}, ${s.last_run.status}` : "never"),
          h("td", { class: "actions" }, h("a", { class: "button", href: `#/compset/${s.id}` }, "Open"))))))));
    } else {
      view.appendChild(h("p", { class: "empty" }, "No compsets yet."));
    }
    view.appendChild(h("h2", {}, "New compset"));
    const name = h("input", { class: "wide", required: true, placeholder: "e.g. London Strand" });
    const city = h("input", { required: true, placeholder: "City" });
    const currency = h("input", { class: "short", required: true, maxlength: 3, value: state.config.currency });
    const nights = h("select", {}, h("option", { value: "1,2" }, "1 and 2 nights"), h("option", { value: "1" }, "1 night"));
    const months = h("select", {}, h("option", { value: "12" }, "12 months of sample weeks"),
      h("option", { value: "3" }, "3 months"), h("option", { value: "0" }, "Events only"));
    const message = h("p", { class: "muted" });
    view.append(h("form", { class: "inline", onsubmit: async (e) => {
      e.preventDefault();
      try {
        const study = await api("/api/studies", { method: "POST", body: JSON.stringify({
          name: name.value, city: city.value, currency: currency.value.toUpperCase(),
          calendar_nights: nights.value.split(",").map(Number), offers_months: Number(months.value) }) });
        location.hash = `#/compset/${study.id}`;
      } catch (error) { message.textContent = error.message; }
    } }, h("label", { class: "field" }, "Name", name), h("label", { class: "field" }, "City", city),
      h("label", { class: "field" }, "Currency", currency), h("label", { class: "field" }, "Calendar", nights),
      h("label", { class: "field" }, "Channel offers", months), h("button", { class: "primary", type: "submit" }, "Create")),
    message);
  }

  async function compsetView(id, params) {
    setNav("compsets");
    const nights = params.get("nights");
    const [study, dash] = await Promise.all([
      api(`/api/studies/${id}`), api(`/api/studies/${id}/dashboard${nights ? `?nights=${nights}` : ""}`)]);
    const target = study.hotels.find((hotel) => hotel.role === "target");
    view.append(h("p", { class: "kicker" }, "Compset"), h("h1", {}, study.name),
      h("p", { class: "meta" }, `${study.city} · ${study.currency} · ${study.adults} adults · calendar ` +
        `${study.calendar_days} days, ${study.calendar_nights.join(" and ")} ` +
        `${study.calendar_nights.length === 1 && study.calendar_nights[0] === 1 ? "night" : "nights"} · channel offers on ` +
        (study.offers_months ? `one week a month for ${study.offers_months} ` +
          `${study.offers_months === 1 ? "month" : "months"} and on event dates` : "event dates only")));

    // Forward view
    view.appendChild(h("h2", {}, "Forward view"));
    if (!dash.series.dates.length) {
      view.appendChild(h("p", { class: "empty" }, target ? "No rates collected yet. Use Collect below."
        : "Add a target hotel and comps, then collect."));
    } else {
      forwardSection(id, study, dash);
    }

    hotelsSection(id, study);
    collectSection(id, study);
    eventsSection(id, study);

    view.appendChild(h("h2", {}, "Report"));
    view.appendChild(h("div", { class: "controls" },
      h("a", { class: "button primary", href: `/api/studies/${id}/report.xlsx` }, "Download Excel"),
      h("span", { class: "muted" }, "Rate position, events, parity, room ladder, sell-out signals and changes.")));
    const shown = new Set(["Rate Shop Overview"]);
    dash.report.filter((table) => !shown.has(table.title) && !table.title.startsWith("Forward Average Rate") &&
      !table.title.startsWith("Target vs Comp Set, Forward")).forEach((table) => {
      view.append(h("h3", {}, table.title), dataTable(table, dash.currency));
    });
  }

  function forwardSection(id, study, dash) {
    const currency = dash.currency;
    if (dash.nights_options.length > 1) {
      view.appendChild(h("div", { class: "segmented", role: "group", "aria-label": "Length of stay" },
        dash.nights_options.map((n) => h("button", { type: "button", "aria-pressed": String(n === dash.nights),
          onclick: () => { location.hash = `#/compset/${id}?nights=${n}`; } }, n === 1 ? "1 night" : `${n} nights`))));
    }
    const rows = dash.forward.rows;
    const byWindow = (days) => rows.find((row) => row[0] === `Next ${days} days`) || [];
    const w30 = byWindow(30);
    const w365 = byWindow(365);
    view.appendChild(h("div", { class: "figures" },
      figure("Target, next 30 days", money(w30[2], currency), `${w30[5] || 0} dates matched to the comp set`),
      figure("Comp set median, next 30 days", money(w30[3], currency), "average of the daily median"),
      figure("Target vs median, next 30 days", pct(w30[4]), ""),
      figure("Target vs median, next 365 days", pct(w365[4]), `${w365[5] || 0} dates`)));
    view.appendChild(h("p", { class: "meta" }, `Snapshot ${fmtDate(dash.as_of)}. Nightly rates for a ` +
      `${dash.nights === 1 ? "1-night" : dash.nights + "-night"} stay, ${study.adults} adults, in ${currency}.`));

    // Chart with range filter
    const s = dash.series;
    const ranges = [30, 90, 180, 365];
    let range = 90;
    const chart = h("div", { class: "chart" });
    const buttons = ranges.map((days) => h("button", { type: "button", "aria-pressed": String(days === range),
      onclick: () => { range = days; buttons.forEach((b, i) => b.setAttribute("aria-pressed", String(ranges[i] === days))); draw(); } },
      days === 365 ? "Year" : `${days} days`));
    const legend = h("div", { class: "legend" },
      s.target ? h("span", { class: "legend-item" }, RateChart.swatchFor({ color: TARGET_COLOR }), s.target.name) : null,
      s.comps.length ? h("span", { class: "legend-item" }, RateChart.swatchFor({ color: MEDIAN_COLOR, dash: "5 3" }), "Comp set median") : null,
      s.comps.length > 1 ? h("span", { class: "legend-item" }, RateChart.bandSwatch(), "Comp set range") : null);
    view.append(h("div", { class: "chart-head" }, legend, h("div", { class: "segmented", role: "group",
      "aria-label": "Date range" }, buttons)), chart);
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
        extra: (i) => s.comps.map((c) => ({ label: c.name, value: c.rates[i] }))
          .sort((a, b) => (b.value ?? -1) - (a.value ?? -1)),
      });
    };
    draw();
    onResize(draw);

    view.appendChild(h("h3", {}, "Target against comp set median"));
    view.appendChild(h("p", { class: "meta" }, "Counts only dates on which the target and at least one comp have a " +
      "price, so both sides average the same dates."));
    view.appendChild(dataTable(dash.forward, currency));
    view.appendChild(h("h3", {}, "Forward average nightly rate by hotel"));
    view.appendChild(h("p", { class: "meta" }, "Each hotel over all its priced dates; the comp set median row " +
      "averages the daily median of the comps."));
    view.appendChild(dataTable(dash.forward_hotels, currency));
  }

  function figure(label, value, note) {
    return h("div", { class: "figure" }, h("div", { class: "label" }, label), h("div", { class: "value" }, value),
      note ? h("div", { class: "note" }, note) : null);
  }

  function hotelsSection(id, study) {
    view.appendChild(h("h2", {}, "Hotels"));
    if (!study.hotels.length) view.appendChild(h("p", { class: "empty" }, "No hotels yet."));
    else {
      view.appendChild(h("div", { class: "table-wrap" }, h("table", { class: "data" },
        h("thead", {}, h("tr", {}, ["Role", "Hotel", "Class", "Rating", ""].map((t) => h("th", {}, t)))),
        h("tbody", {}, study.hotels.map((hotel) => h("tr", { class: hotel.role === "target" ? "target" : null },
          h("td", {}, hotel.role === "target" ? "Target" : "Comp"),
          h("td", { class: "wrap" }, hotel.name),
          h("td", {}, hotel.hotel_class ? `${hotel.hotel_class}-star` : "–"),
          h("td", {}, hotel.rating ? `${hotel.rating} (${(hotel.reviews_count || 0).toLocaleString("en-GB")})` : "–"),
          h("td", { class: "actions" },
            h("a", { class: "button", href: `#/hotel/${encodeURIComponent(hotel.source_id)}?name=${encodeURIComponent(hotel.name)}&currency=${study.currency}` }, "Live rates"),
            h("button", { type: "button", onclick: async () => {
              if (!confirm(`Remove ${hotel.name} from ${study.name}? Its collected rates are kept.`)) return;
              await api(`/api/studies/${id}/hotels/${hotel.id}`, { method: "DELETE" });
              route();
            } }, "Remove"))))))));
    }
    view.appendChild(h("div", { class: "controls" },
      h("a", { class: "button", href: `#/search?study=${id}` }, "Add hotels")));
  }

  function collectSection(id, study) {
    view.appendChild(h("h2", {}, "Collect"));
    const seconds = state.config.seconds_per_request;
    const hotels = study.hotels.length;
    const calendarRequests = hotels * study.calendar_nights.length;
    const offerDates = Math.round(study.offers_months * 7 * 0.85) + study.events.length;
    const minutes = (requests) => Math.max(1, Math.round((requests * seconds) / 60));
    const latest = study.runs[0];
    const panel = h("div");
    const start = (calendarOnly) => async () => {
      try {
        const { run_id: runId } = await api(`/api/studies/${id}/runs`, { method: "POST",
          body: JSON.stringify({ calendar_only: calendarOnly }) });
        watch(runId);
      } catch (error) { panel.textContent = ""; panel.appendChild(status(error.message, true)); }
    };
    view.appendChild(h("div", { class: "controls" },
      h("button", { type: "button", class: "primary", disabled: !hotels, onclick: start(true) }, "Collect calendars"),
      h("span", { class: "muted" }, `about ${minutes(calendarRequests)} min`),
      h("button", { type: "button", disabled: !hotels, onclick: start(false) }, "Full collection"),
      h("span", { class: "muted" }, `calendars plus channel offers on about ${offerDates} dates per hotel, ` +
        `about ${minutes(calendarRequests + hotels * offerDates)} min`)));
    view.appendChild(h("p", { class: "muted" }, "Rates already collected today are reused, so running again " +
      "the same day only fetches what is missing or failed."));
    view.appendChild(panel);
    if (latest) {
      renderRun(panel, latest);
      if (latest.active) watch(latest.id);
    }

    function watch(runId) {
      stopPolling();
      const tick = async () => {
        try {
          const run = await api(`/api/runs/${runId}`);
          renderRun(panel, run);
          if (!run.active) { stopPolling(); if (run.status !== "running") setTimeout(route, 600); }
        } catch (error) { stopPolling(); panel.appendChild(status(error.message, true)); }
      };
      tick();
      state.poll = setInterval(tick, 3000);
    }
  }

  function renderRun(panel, run) {
    panel.textContent = "";
    const counts = Object.entries(run.counts || {}).filter(([, c]) => Object.keys(c).length)
      .map(([task, c]) => `${task}: ` + Object.entries(c).map(([k, v]) => `${v} ${k}`).join(", ")).join(" · ");
    const label = run.active ? "Collecting…" : `Last collection ${fmtTime(run.started_at)}: ${run.status}`;
    panel.append(h("p", {}, h("b", {}, label), counts ? ` · ${counts}` : "",
      run.requests_sent ? ` · ${run.requests_sent} requests` : ""));
    if (run.status === "running" && !run.active) {
      panel.appendChild(h("p", { class: "muted" }, "This collection is not running in this app: it was interrupted " +
        "or runs elsewhere. Start it again to resume; collected rates are kept."));
    }
    if (run.error) panel.appendChild(h("p", { class: "annotation" }, run.error));
    if (run.messages && run.messages.length) panel.appendChild(h("div", { class: "run-log" }, run.messages.join("\n")));
  }

  function eventsSection(id, study) {
    view.appendChild(h("h2", {}, `Events in ${study.city}`));
    if (study.events.length) {
      view.appendChild(dataTable({ columns: [{ name: "Event" }, { name: "From", kind: "date" }, { name: "To", kind: "date" }],
        rows: study.events.map((e) => [e.name, e.event_date, e.end_date || e.event_date]), total: null }, study.currency));
    } else view.appendChild(h("p", { class: "empty" }, "No events yet. Event dates get channel offers and an uplift analysis."));
    const name = h("input", { required: true, placeholder: "Event name" });
    const from = h("input", { type: "date", required: true });
    const to = h("input", { type: "date" });
    const message = h("span", { class: "muted" });
    view.appendChild(h("form", { class: "inline", onsubmit: async (e) => {
      e.preventDefault();
      try {
        await api("/api/events", { method: "POST", body: JSON.stringify({ city: study.city, name: name.value,
          event_date: from.value, end_date: to.value || null, country: study.country }) });
        route();
      } catch (error) { message.textContent = error.message; }
    } }, h("label", { class: "field" }, "Event", name), h("label", { class: "field" }, "From", from),
      h("label", { class: "field" }, "To (optional)", to), h("button", { type: "submit" }, "Add event"), message));
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
  route();
})();

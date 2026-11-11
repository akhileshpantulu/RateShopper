/* Map view on Leaflet. Target: filled amber, comps: filled cyan, candidates: hollow dashed grey rings.
   Layers: a muted basemap by default (switchable to detailed or none), and toggles for hotel labels, candidates and
   distance rings; the choice is remembered in this browser. Labels and popups are DOM nodes, so hotel names from
   Google are never parsed as HTML. */
(function () {
  "use strict";

  const STYLE = {
    target: { radius: 8, color: "#0B0F12", weight: 2, fillColor: "#C8811A", fillOpacity: 1 },
    comp: { radius: 6, color: "#0B0F12", weight: 2, fillColor: "#2390B6", fillOpacity: 1 },
    candidate: { radius: 5, color: "#8A979F", weight: 1.2, dashArray: "2 3", fillColor: "#8A979F", fillOpacity: 0.05 },
  };
  const RINGS_KM = [0.5, 1, 2];
  const LABEL_MAX = 24;
  const BASES = { muted: "Muted streets", detailed: "Detailed streets", none: "No basemap" };
  const OVERLAYS = { labels: "Hotel labels", candidates: "Candidates", rings: "Distance rings" };
  const PREFS_KEY = "rateshop.map.layers";

  function loadPrefs() {
    try {
      const stored = JSON.parse(localStorage.getItem(PREFS_KEY) || "null");
      if (stored && BASES[stored.base] && Array.isArray(stored.hidden)) return stored;
    } catch (_) { /* storage unavailable: use defaults */ }
    return { base: "muted", hidden: [] };
  }

  function savePrefs(prefs) {
    try { localStorage.setItem(PREFS_KEY, JSON.stringify(prefs)); } catch (_) { /* not critical */ }
  }

  function shortName(name) {
    const text = String(name || "");
    return text.length > LABEL_MAX ? text.slice(0, LABEL_MAX - 1) + "…" : text;
  }

  function textNode(text) {
    const span = document.createElement("span");
    span.textContent = text;
    return span;
  }

  function create(element, config) {
    const map = L.map(element, { zoomControl: true, attributionControl: true });
    map.attributionControl.setPrefix("Leaflet");
    map.setView([30, 0], 2);

    const tileOptions = { attribution: config.map_tile_attribution, maxZoom: 19 };
    const dark = config.map_tile_dark_filter;
    const bases = {
      muted: L.tileLayer(config.map_tile_url, { ...tileOptions, className: dark ? "tiles-muted" : "tiles-muted-plain" }),
      detailed: L.tileLayer(config.map_tile_url, { ...tileOptions, className: dark ? "tiles-detailed" : "" }),
      none: L.layerGroup(),
    };
    const hotels = L.layerGroup().addTo(map);
    const overlays = { labels: L.layerGroup(), candidates: L.layerGroup(), rings: L.layerGroup() };

    const prefs = loadPrefs();
    bases[prefs.base].addTo(map);
    Object.entries(overlays).forEach(([key, layer]) => { if (!prefs.hidden.includes(key)) layer.addTo(map); });
    const named = (source, names) => Object.fromEntries(Object.entries(source).map(([key, layer]) => [names[key], layer]));
    L.control.layers(named(bases, BASES), named(overlays, OVERLAYS), { position: "topright", collapsed: true }).addTo(map);
    const keyOf = (layer, source) => Object.keys(source).find((key) => source[key] === layer);
    map.on("baselayerchange", (e) => { prefs.base = keyOf(e.layer, bases) || "muted"; savePrefs(prefs); });
    map.on("overlayadd overlayremove", (e) => {
      const key = keyOf(e.layer, overlays);
      if (!key) return;
      prefs.hidden = prefs.hidden.filter((item) => item !== key);
      if (e.type === "overlayremove") prefs.hidden.push(key);
      savePrefs(prefs);
    });

    // The container can change size after creation (a panel growing beside it): keep the map in step.
    if (window.ResizeObserver) new ResizeObserver(() => map.invalidateSize()).observe(element);

    const entries = new Map(); // key -> { marker, label }

    function removeEntry(key) {
      const entry = entries.get(key);
      if (!entry) return;
      entry.marker.remove();
      if (entry.label) entry.label.remove();
      entries.delete(key);
    }

    return {
      map,
      clear() {
        hotels.clearLayers();
        Object.values(overlays).forEach((layer) => layer.clearLayers());
        entries.clear();
      },
      /** points: [{key, role, latitude, longitude, label, popup: () => Node}] */
      plot(points, options = {}) {
        points.forEach((point) => {
          if (point.latitude == null || point.longitude == null) return;
          removeEntry(point.key);
          const position = [point.latitude, point.longitude];
          const isCandidate = !STYLE[point.role] || point.role === "candidate";
          const marker = L.circleMarker(position, STYLE[point.role] || STYLE.candidate);
          // Hover always names the hotel; the permanent label layer can be switched off.
          if (point.label) marker.bindTooltip(textNode(point.label), { direction: "right", offset: [8, 0], className: "map-label" });
          if (point.popup) marker.bindPopup(() => point.popup(), { maxWidth: 320 });
          marker.addTo(isCandidate ? overlays.candidates : hotels);
          let label = null;
          if (!isCandidate && point.label) {
            label = L.tooltip({ permanent: true, direction: "right", offset: [8, 0], interactive: false,
                                className: "map-label" + (point.role === "target" ? " map-label-target" : "") })
              .setLatLng(position).setContent(textNode(shortName(point.label)));
            overlays.labels.addLayer(label);
          }
          if (point.role === "target") marker.bringToFront();
          entries.set(point.key, { marker, label });
        });
        if (options.fit !== false) this.fit();
      },
      remove: removeEntry,
      fit() {
        const located = [...entries.values()].map((entry) => entry.marker.getLatLng());
        if (!located.length) return;
        if (located.length === 1) { map.setView(located[0], 15); return; }
        // Zoom 16 and closer is where OpenStreetMap draws point-of-interest icons; stop just short of it.
        map.fitBounds(L.latLngBounds(located).pad(0.2), { maxZoom: 15 });
      },
      rings(latitude, longitude) {
        overlays.rings.clearLayers();
        if (latitude == null || longitude == null) return;
        RINGS_KM.forEach((km) => {
          L.circle([latitude, longitude], { radius: km * 1000, color: "#8A979F", opacity: 0.35, weight: 1,
                                            dashArray: "2 6", fill: false, interactive: false }).addTo(overlays.rings);
          const north = L.latLng(latitude + km / 111.32, longitude);
          L.tooltip({ permanent: true, direction: "top", className: "ring-label", offset: [0, -1], interactive: false })
            .setLatLng(north).setContent(textNode(km < 1 ? `${km * 1000} m` : `${km} km`)).addTo(overlays.rings);
        });
      },
      focus(key) {
        const entry = entries.get(key);
        if (!entry) return false;
        if (!map.hasLayer(entry.marker)) overlays.candidates.addTo(map);
        map.setView(entry.marker.getLatLng(), Math.max(map.getZoom(), 15));
        entry.marker.openPopup();
        return true;
      },
      invalidate() { map.invalidateSize(); },
    };
  }

  function popup(title, rows, actions) {
    const box = document.createElement("div");
    const head = document.createElement("div");
    head.className = "popup-title";
    head.textContent = title;
    box.appendChild(head);
    rows.filter(([, value]) => value != null && value !== "").forEach(([key, value]) => {
      const row = document.createElement("div");
      row.className = "popup-row";
      const k = document.createElement("span");
      k.className = "k";
      k.textContent = key;
      const v = document.createElement("span");
      v.textContent = value;
      row.append(k, v);
      box.appendChild(row);
    });
    if (actions && actions.length) {
      const bar = document.createElement("div");
      bar.className = "popup-actions";
      actions.forEach((action) => bar.appendChild(action));
      box.appendChild(bar);
    }
    return box;
  }

  function formatCoords(latitude, longitude) {
    if (latitude == null || longitude == null) return null;
    const lat = `${Math.abs(latitude).toFixed(4)}°${latitude >= 0 ? "N" : "S"}`;
    const lon = `${Math.abs(longitude).toFixed(4)}°${longitude >= 0 ? "E" : "W"}`;
    return `${lat} ${lon}`;
  }

  window.RateMap = { create, popup, formatCoords };
})();

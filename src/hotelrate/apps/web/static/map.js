/* Map view on Leaflet. Target: filled amber, comps: filled cyan, candidates: hollow dashed grey rings.
   Labels and popups are built as DOM nodes, so hotel names from Google are never parsed as HTML. */
(function () {
  "use strict";

  const STYLE = {
    target: { radius: 9, color: "#0B0F12", weight: 2, fillColor: "#C8811A", fillOpacity: 1 },
    comp: { radius: 7, color: "#0B0F12", weight: 2, fillColor: "#2390B6", fillOpacity: 1 },
    candidate: { radius: 7, color: "#8A979F", weight: 1.5, dashArray: "3 3", fillColor: "#8A979F", fillOpacity: 0.08 },
  };
  const RINGS_KM = [0.5, 1, 2];

  function create(element, config) {
    const map = L.map(element, { zoomControl: true, worldCopyJump: true, attributionControl: true });
    map.attributionControl.setPrefix("Leaflet");
    L.tileLayer(config.map_tile_url, { attribution: config.map_tile_attribution, maxZoom: 19,
                                       className: config.map_tile_dark_filter ? "dark-tiles" : "" }).addTo(map);
    map.setView([30, 0], 2);
    const layer = L.layerGroup().addTo(map);
    const rings = L.layerGroup().addTo(map);
    const markers = new Map();
    // The container can change size after creation (a panel growing beside it): keep the map in step.
    if (window.ResizeObserver) new ResizeObserver(() => map.invalidateSize()).observe(element);
    return {
      map,
      clear() { layer.clearLayers(); rings.clearLayers(); markers.clear(); },
      /** points: [{key, role, latitude, longitude, label, popup: () => Node}] */
      plot(points, options = {}) {
        points.forEach((point) => {
          if (point.latitude == null || point.longitude == null) return;
          const old = markers.get(point.key);
          if (old) layer.removeLayer(old);
          const marker = L.circleMarker([point.latitude, point.longitude], STYLE[point.role] || STYLE.candidate);
          if (point.label && point.role !== "candidate") {
            const label = document.createElement("span");
            label.textContent = point.label;
            marker.bindTooltip(label, { permanent: true, direction: "right", offset: [10, 0], className: "map-label" });
          } else if (point.label) {
            const label = document.createElement("span");
            label.textContent = point.label;
            marker.bindTooltip(label, { direction: "right", offset: [10, 0], className: "map-label" });
          }
          if (point.popup) marker.bindPopup(() => point.popup(), { maxWidth: 320 });
          if (point.onClick) marker.on("click", point.onClick);
          marker.addTo(layer);
          if (point.role === "target") marker.bringToFront();
          markers.set(point.key, marker);
        });
        if (options.fit !== false) this.fit();
      },
      remove(key) {
        const marker = markers.get(key);
        if (marker) { layer.removeLayer(marker); markers.delete(key); }
      },
      fit() {
        const located = [...markers.values()].map((m) => m.getLatLng());
        if (!located.length) return;
        if (located.length === 1) { map.setView(located[0], 15); return; }
        map.fitBounds(L.latLngBounds(located).pad(0.25), { maxZoom: 16 });
      },
      rings(latitude, longitude) {
        rings.clearLayers();
        if (latitude == null || longitude == null) return;
        RINGS_KM.forEach((km) => {
          const circle = L.circle([latitude, longitude], { radius: km * 1000, color: "#5F6C74", weight: 1,
                                                          dashArray: "4 4", fill: false, interactive: false });
          circle.addTo(rings);
          const north = L.latLng(latitude + km / 111.32, longitude);
          const tag = document.createElement("span");
          tag.textContent = km < 1 ? `${km * 1000} M` : `${km} KM`;
          L.tooltip({ permanent: true, direction: "top", className: "ring-label", offset: [0, -2] })
            .setLatLng(north).setContent(tag).addTo(rings);
        });
      },
      focus(key) {
        const marker = markers.get(key);
        if (!marker) return false;
        map.setView(marker.getLatLng(), Math.max(map.getZoom(), 15));
        marker.openPopup();
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

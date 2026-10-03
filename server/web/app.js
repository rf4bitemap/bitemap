/* BiteMap website. Plain JS + Leaflet, talks to /api/v1. */
(function () {
  'use strict';
  const RELEASES_URL = 'https://github.com/rf4bitemap/bitemap/releases/latest';
  const $ = (s) => document.querySelector(s);
  const state = { lang: 'en', meta: null, fish: {}, waters: {}, map: null, layers: [], tab: 'map' };
  window.__bitemap = state;  // handy in the browser console

  // ------------------------------------------------------------------ i18n
  function pickLang() {
    const saved = localStorage.getItem('bitemap.lang');
    if (saved && I18N[saved]) return saved;
    const nav = (navigator.language || 'en').slice(0, 2);
    return I18N[nav] ? nav : 'en';
  }
  function t(key, vars) {
    let s = (I18N[state.lang] || {})[key] || I18N.en[key] || key;
    if (vars) for (const k in vars) s = s.replace('{' + k + '}', vars[k]);
    return s;
  }
  function applyI18n() {
    document.documentElement.lang = state.lang;
    document.querySelectorAll('[data-i18n]').forEach((el) => { el.textContent = t(el.dataset.i18n); });
  }
  const fishName = (id) => (state.fish[id] && (state.fish[id].names[state.lang] || state.fish[id].names.en)) || id;
  const waterName = (id) => (state.waters[id] && (state.waters[id].names[state.lang] || state.waters[id].names.en)) || id;

  // ------------------------------------------------------------------ helpers
  async function api(path, params) {
    const q = params ? '?' + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v != null)) : '';
    const r = await fetch('/api/v1' + path + q);
    if (!r.ok) throw new Error(r.status + ' ' + path);
    return r.json();
  }
  function fmtW(g) {
    if (!g) return '–';
    return g >= 1000 ? (g / 1000).toFixed(3) + ' kg' : g + ' g';
  }
  function ago(iso) {
    const m = Math.max(1, Math.round((Date.now() - new Date(iso).getTime()) / 60000));
    if (m < 60) return t('ago_m', { n: m });
    if (m < 60 * 48) return t('ago_h', { n: Math.round(m / 60) });
    return t('ago_d', { n: Math.round(m / 1440) });
  }
  function esc(s) { return String(s).replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c])); }
  function fillSelect(sel, items, value) {
    sel.innerHTML = items.map(([v, label]) => `<option value="${esc(v)}">${esc(label)}</option>`).join('');
    if (value != null) sel.value = value;
  }

  // ------------------------------------------------------------------ tabs
  function showTab(name) {
    state.tab = name;
    document.querySelectorAll('.tab').forEach((s) => { s.hidden = s.id !== 'tab-' + name; });
    document.querySelectorAll('nav a').forEach((a) => a.classList.toggle('on', a.dataset.tab === name));
    if (name === 'map') { setTimeout(() => state.fitBounds && fit(state.fitBounds), 60); loadSpots(); }
    if (name === 'fish') loadFish();
    if (name === 'trophies') loadOverview();
  }

  // ------------------------------------------------------------------ map
  // Game coordinates are used directly in Leaflet's CRS.Simple: lat = y (north/up), lng = x.
  // A map image spans exactly [min_x..max_x] x [min_y..max_y] (data/waterbodies.json).
  const P = (x, y) => [y, x];
  function fit(b) {
    state.fitBounds = b;
    const map = state.map;
    map.invalidateSize(false);
    map.setMinZoom(-8);   // getBoundsZoom clamps to the current limits, which belong to the previous map
    map.setMaxZoom(12);
    const z = map.getBoundsZoom(b, false, [20, 20]);
    map.setMinZoom(z - 1);
    map.setMaxZoom(z + 4);
    map.fitBounds(b, { padding: [20, 20], animate: false });
  }
  function drawBase(waterId, spots) {
    const map = state.map;
    state.layers.forEach((l) => map.removeLayer(l));
    state.layers = [];
    const w = state.waters[waterId];
    const m = w && w.map;
    if (m && m.image) {
      const b = [P(m.min_x, m.min_y), P(m.max_x, m.max_y)];
      state.layers.push(L.imageOverlay('/maps/' + m.image, b).addTo(map));
      map.setMaxBounds(L.latLngBounds(b).pad(0.25));
      fit(b);
      $('#map').dataset.nomap = '';
      return;
    }
    const lo = 0;
    const seen = Math.max(0, ...(spots || []).map((s) => Math.max(s.x, s.y)));
    const hi = Math.min(((w && w.coord_range) || [0, 999])[1], Math.max(100, Math.ceil((seen + 5) / 20) * 20));
    const g = [];
    for (let v = lo; v <= hi; v += 10) {
      const style = { color: '#2c3a40', weight: v % 50 ? 1 : 2, interactive: false };
      g.push(L.polyline([P(v, lo), P(v, hi)], style), L.polyline([P(lo, v), P(hi, v)], style));
      if (v % 20 === 0) {
        g.push(L.marker(P(v, lo), { icon: L.divIcon({ className: 'axis', html: v, iconSize: [30, 14], iconAnchor: [15, -4] }), interactive: false }));
        g.push(L.marker(P(lo, v), { icon: L.divIcon({ className: 'axis', html: v, iconSize: [30, 14], iconAnchor: [34, 7] }), interactive: false }));
      }
    }
    state.layers.push(L.layerGroup(g).addTo(map));
    map.setMaxBounds(null);
    fit([P(lo, lo), P(hi, hi)]);
    $('#map').dataset.nomap = t('no_map');
  }

  let spotReq = 0;
  async function loadSpots() {
    const water = $('#f-water').value;
    if (!water) return;
    localStorage.setItem('bitemap.water', water);
    const req = ++spotReq;
    const data = await api('/stats/spots', { water, fish: $('#f-fish').value, days: $('#f-days').value,
      level: $('#f-level').value });
    if (req !== spotReq) return;  // a newer filter change is already loading
    const spots = data.spots;
    drawBase(water, spots);
    const max = Math.max(1, ...spots.map((s) => s.count));
    if (spots.length && L.heatLayer && state.map.getSize().x > 0) {  // leaflet.heat can't draw into a 0px map
      try {
        const heat = L.heatLayer(spots.map((s) => [...P(s.x, s.y), s.count / max]),
          { radius: 28, blur: 22, maxZoom: 2, minOpacity: 0.25 });
        state.layers.push(heat.addTo(state.map));
      } catch (e) { console.warn('heat layer', e); }
    }
    const markers = spots.slice(0, 60).map((s) => {
      const r = 5 + 10 * Math.sqrt(s.count / max);
      return L.circleMarker(P(s.x, s.y), { radius: r, color: '#ffd166', weight: 1.5, fillColor: '#ef8354', fillOpacity: 0.55 })
        .bindPopup(spotPopup(s));
    });
    state.layers.push(L.layerGroup(markers).addTo(state.map));
    $('#spot-list').innerHTML = spots.slice(0, 25).map((s, i) =>
      `<li data-i="${i}"><b>${s.x}:${s.y}</b> <span>${s.count} ${t('catches')}</span><br><small>${
        s.top_fish.slice(0, 3).map((f) => esc(fishName(f.fish_id)) + ' ×' + f.count).join(', ')}</small></li>`).join('');
    $('#spot-empty').hidden = spots.length > 0;
    document.querySelectorAll('#spot-list li').forEach((li) => li.addEventListener('click', () => {
      const m = markers[+li.dataset.i];
      if (m) { state.map.setView(m.getLatLng(), Math.max(state.map.getZoom(), 1)); m.openPopup(); }
    }));
  }
  function spotPopup(s) {
    return `<div class="pop"><h4>${s.x}:${s.y}</h4>
      <p>${s.count} ${t('catches')} · ${s.anglers} ${t('anglers')}${trophyCounts(s)}</p>
      <p>${t('biggest')}: ${fmtW(s.max_weight_g)}</p>
      <ul>${s.top_fish.map((f) => `<li>${esc(fishName(f.fish_id))} <b>×${f.count}</b></li>`).join('')}</ul></div>`;
  }

  // ------------------------------------------------------------------ fish + trophies
  // trophy levels come from the fish's trophy / super trophy weights (super trophies count as trophies too)
  const TROPHY = '🏆', SUPER = '👑';
  function trophyCounts(s) {
    const normal = (s.trophies || 0) - (s.super_trophies || 0);
    return (normal > 0 ? ` · ${TROPHY} ${normal}` : '') + (s.super_trophies ? ` · ${SUPER} ${s.super_trophies}` : '');
  }
  async function loadFish() {
    const data = await api('/stats/fish', { water: $('#fish-water').value, days: $('#fish-days').value });
    $('#fish-rows').innerHTML = data.fish.map((f) => `<tr><td>${esc(fishName(f.fish_id))}</td><td>${f.count}</td>
      <td>${fmtW(f.avg_weight_g)}</td><td>${fmtW(f.max_weight_g)}</td><td>${trophyCounts(f).replace(/^ · /, '').replace(/ · /g, ' ')}</td>
      <td>${f.best_spot ? esc(waterName(f.best_spot.waterbody)) + ' ' + f.best_spot.x + ':' + f.best_spot.y : ''}</td></tr>`).join('')
      || `<tr><td colspan="6" class="muted">${t('no_data')}</td></tr>`;
  }
  async function loadOverview() {
    const o = await api('/stats/overview');
    $('#st-total').textContent = o.catches_total.toLocaleString();
    $('#st-week').textContent = o.catches_7d.toLocaleString();
    $('#st-anglers').textContent = o.anglers_7d.toLocaleString();
    $('#trophy-rows').innerHTML = o.recent_trophies.map((c) => `<tr><td>${ago(c.caught_at)}</td>
      <td title="${t(c.super ? 'lvl_2' : 'lvl_1')}">${c.super ? SUPER : TROPHY} ${esc(fishName(c.fish_id))}</td><td>${fmtW(c.weight_g)}</td><td>${esc(waterName(c.waterbody))}</td>
      <td>${c.x}:${c.y}</td></tr>`).join('') || `<tr><td colspan="5" class="muted">${t('no_data')}</td></tr>`;
  }

  // ------------------------------------------------------------------ boot
  function fillFilters() {
    const waters = Object.keys(state.waters).sort((a, b) => waterName(a).localeCompare(waterName(b)));
    const saved = localStorage.getItem('bitemap.water');
    fillSelect($('#f-water'), waters.map((w) => [w, waterName(w)]), $('#f-water').value || (state.waters[saved] ? saved : waters[0]));
    fillSelect($('#fish-water'), [['', t('all_waters')], ...waters.map((w) => [w, waterName(w)])], $('#fish-water').value);
    fillFishFilter();
  }
  function fillFishFilter() {
    // only the fish that live in the selected waterbody (fish without that data: everywhere)
    const water = $('#f-water').value;
    const fish = Object.keys(state.fish).filter((f) => !state.fish[f].waters || state.fish[f].waters.includes(water))
      .sort((a, b) => fishName(a).localeCompare(fishName(b)));
    const cur = $('#f-fish').value;
    fillSelect($('#f-fish'), [['', t('all_fish')], ...fish.map((f) => [f, fishName(f)])], fish.includes(cur) ? cur : '');
  }

  async function boot() {
    state.lang = pickLang();
    $('#lang').value = state.lang;
    applyI18n();
    $('#dl-link').href = RELEASES_URL;
    state.map = L.map('map', { crs: L.CRS.Simple, minZoom: -4, maxZoom: 8, zoomSnap: 0.25, attributionControl: false });
    let lastW = 0;
    new ResizeObserver(() => {  // the map may be laid out after the first fit (hidden tab, late CSS)
      const w = $('#map').clientWidth;
      if (w && !lastW && state.tab === 'map') loadSpots();          // became visible: redraw everything
      else if (w && Math.abs(w - lastW) > 40 && state.fitBounds) fit(state.fitBounds);
      lastW = w;
    }).observe($('#map'));
    state.meta = await api('/meta');
    state.meta.fish.forEach((f) => { state.fish[f.id] = f; });
    state.meta.waterbodies.forEach((w) => { state.waters[w.id] = w; });
    fillFilters();
    $('#f-water').addEventListener('change', fillFishFilter);   // registered first: the fish list must match the water
    ['#f-water', '#f-fish', '#f-days', '#f-level'].forEach((s) => $(s).addEventListener('change', loadSpots));
    ['#fish-water', '#fish-days'].forEach((s) => $(s).addEventListener('change', loadFish));
    $('#lang').addEventListener('change', (e) => {
      state.lang = e.target.value; localStorage.setItem('bitemap.lang', state.lang);
      applyI18n(); fillFilters(); showTab(state.tab); loadOverview();
    });
    document.querySelectorAll('nav a').forEach((a) => a.addEventListener('click', (e) => {
      e.preventDefault(); history.replaceState(null, '', '#' + a.dataset.tab); showTab(a.dataset.tab);
    }));
    loadOverview();
    const h = location.hash.slice(1);
    showTab(['map', 'fish', 'trophies', 'download'].includes(h) ? h : 'map');
    setInterval(() => { if (!document.hidden) { loadOverview(); if (state.tab === 'map') loadSpots(); } }, 120000);
  }
  boot().catch((e) => { console.error(e); });
})();

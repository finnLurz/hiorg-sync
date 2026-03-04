// /static/groupmap.js

let STATE = {
  ov: (window.HIORG_OV || "").trim(),
  ldapLocations: [], // [{key, base_dn}]
  discoveryGroups: [], // [{group, locations:[...]}]
  map: { version: 2, locations: {}, groups: {}, notify: {} },
};

function esc(s) {
  return String(s ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function api(url) {
  return fetch(url, { credentials: "same-origin" });
}

async function apiJson(url) {
  const r = await api(url);
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return await r.json();
}

async function apiPostJson(url, body) {
  const r = await fetch(url, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error(`${r.status} ${await r.text()}`);
  return await r.json();
}

/** normalize /api/ldap/locations response to [{key, base_dn}] */
function normalizeLocations(payload) {
  const locs = payload && payload.locations ? payload.locations : [];
  if (Array.isArray(locs) && locs.length > 0) {
    if (typeof locs[0] === "string") {
      return locs
        .map((k) => ({ key: String(k).trim(), base_dn: "" }))
        .filter((x) => x.key);
    }
    return locs
      .map((x) => ({
        key: String(x?.key ?? "").trim(),
        base_dn: String(x?.base_dn ?? "").trim(),
      }))
      .filter((x) => x.key);
  }
  return [];
}

/** ensure minimal schema */
function normalizeMap(m) {
  if (!m || typeof m !== "object") m = {};
  if (typeof m.version !== "number") m.version = 2;

  if (!m.locations || typeof m.locations !== "object") m.locations = {};
  if (!m.groups || typeof m.groups !== "object") m.groups = {};
  if (!m.notify || typeof m.notify !== "object") m.notify = {};

  // normalize location keys to lowercase
  const locNorm = {};
  for (const [k, v] of Object.entries(m.locations)) {
    const kk = String(k).trim().toLowerCase();
    if (!kk) continue;
    locNorm[kk] = v && typeof v === "object" ? v : {};
  }
  m.locations = locNorm;

  // groups: keep group name as-is, normalize location + ad_cn
  const grpNorm = {};
  for (const [g, cfg] of Object.entries(m.groups)) {
    const gg = String(g).trim();
    if (!gg) continue;
    const cc = cfg && typeof cfg === "object" ? { ...cfg } : {};
    if (cc.location != null) cc.location = String(cc.location).trim().toLowerCase();
    if (cc.ad_cn != null) cc.ad_cn = String(cc.ad_cn).trim();
    grpNorm[gg] = cc;
  }
  m.groups = grpNorm;

  return m;
}

function getLocationOptionsHtml(selectedKey) {
  const sel = String(selectedKey || "").trim().toLowerCase();
  const opts = [];
  opts.push(`<option value="">(kein Standort)</option>`);

  for (const loc of STATE.ldapLocations) {
    const k = String(loc.key).trim().toLowerCase();
    if (!k) continue;

    const label = loc.base_dn ? `${k} — ${loc.base_dn}` : k;

    opts.push(
      `<option value="${esc(k)}" ${k === sel ? "selected" : ""}>${esc(label)}</option>`
    );
  }
  return opts.join("");
}

/** ---------- Notify UI ---------- */
function setNotifyEnabled(v) {
  STATE.map.notify = STATE.map.notify || {};
  STATE.map.notify.enabled = !!v;
}

function setNotify(field, value) {
  STATE.map.notify = STATE.map.notify || {};
  STATE.map.notify[field] = String(value || "").trim();
}

function setNotifyFreq(v) {
  STATE.map.notify = STATE.map.notify || {};
  const n = parseInt(v || "0", 10);
  STATE.map.notify.freq_hours = isNaN(n) ? 0 : n;
}

function renderNotify() {
  const n = STATE.map.notify || {};

  const elEnabled = document.getElementById("notify_enabled");
  const elTo = document.getElementById("notify_to");
  const elSubject = document.getElementById("notify_subject");
  const elFreq = document.getElementById("notify_freq");

  if (elEnabled) elEnabled.checked = !!n.enabled;
  if (elTo) elTo.value = String(n.to || "");
  if (elSubject) elSubject.value = String(n.subject || "");
  if (elFreq) elFreq.value = String(n.freq_hours ?? 0);
}

/** ---------- Locations block (optional, only structure) ---------- */
function renderLocationsBox() {
  const el = document.getElementById("locsc");
  if (!el) return; // wichtig: manche Seiten haben keinen Locations-Block

  const locs = STATE.map.locations || {};
  const rows = [];

  for (const key of Object.keys(locs).sort()) {
    rows.push(`
      <div class="fwRow" style="grid-template-columns: 2fr 1fr; gap:10px;">
        <input class="fwInput" value="${esc(key)}" disabled>
        <button class="fwBtn2" type="button" onclick="removeLoc('${esc(key)}')">Entfernen</button>
      </div>
    `);
  }

  el.innerHTML = rows.length
    ? rows.join("")
    : `<div class="fwHint">Keine Standorte im Mapping hinterlegt.</div>`;
}

function addLoc() {
  const key = prompt("Standort-Key (z.B. mitte, bommersheim):");
  if (!key) return;
  const k = String(key).trim().toLowerCase();
  if (!k) return;

  STATE.map.locations = STATE.map.locations || {};
  if (!STATE.map.locations[k]) STATE.map.locations[k] = {};
  renderLocationsBox();
}

function removeLoc(key) {
  const k = String(key || "").trim().toLowerCase();
  if (!k) return;
  if (!confirm(`Standort '${k}' wirklich entfernen?`)) return;

  if (STATE.map.locations) delete STATE.map.locations[k];

  // remove references from groups
  for (const g of Object.keys(STATE.map.groups || {})) {
    const cfg = STATE.map.groups[g] || {};
    if ((cfg.location || "").toLowerCase() === k) cfg.location = "";
  }

  renderLocationsBox();
  renderGroupsTable();
}

/** ---------- Groups table ---------- */
function renderGroupsTable() {
  const host = document.getElementById("groups");
  if (!host) return;

  const disc = Array.isArray(STATE.discoveryGroups) ? STATE.discoveryGroups : [];
  const rows = [];

  for (const item of disc) {
    const gname = String(item.group || "").trim();
    if (!gname) continue;

    const cfg = (STATE.map.groups || {})[gname] || {};
    const loc = String(cfg.location || "").trim().toLowerCase();
    const adcn = String(cfg.ad_cn || "").trim();

    // WICHTIG:
    // - data-group: damit wir beim Speichern DOM -> STATE syncen können
    rows.push(`
      <div class="gmRow">
        <div class="gmName">${esc(gname)}</div>

        <select class="fwInput"
          onchange="setGroupLocation(${JSON.stringify(gname)}, this.value)">
          ${getLocationOptionsHtml(loc)}
    </select>

    <input class="fwInput"
           placeholder="z.B. Mitte_Atemschutz (CN)"
           value="${esc(adcn)}"
           oninput="setGroupCn(${JSON.stringify(gname)}, this.value)">
  </div>
`);

  }

  host.innerHTML = rows.length
    ? rows.join("")
    : `<div class="fwHint">Keine Gruppen gefunden (Discovery leer).</div>`;
}

function setGroupLocation(groupName, locKey) {
  const g = String(groupName || "").trim();
  if (!g) return;

  STATE.map.groups = STATE.map.groups || {};
  STATE.map.groups[g] = STATE.map.groups[g] || {};
  STATE.map.groups[g].location = String(locKey || "").trim().toLowerCase();
}

function setGroupCn(groupName, cn) {
  const g = String(groupName || "").trim();
  if (!g) return;

  STATE.map.groups = STATE.map.groups || {};
  STATE.map.groups[g] = STATE.map.groups[g] || {};
  STATE.map.groups[g].ad_cn = String(cn || "").trim();
}

/**
 * ROBUST: Beim Speichern immer alles aus dem DOM einsammeln.
 * (sonst kann es passieren, dass CN zwar im Input steht, aber nie im STATE landet)
 */
function syncGroupsFromDom() {
  // Standort
  document.querySelectorAll('#groups select[data-group]').forEach((sel) => {
    const g = String(sel.getAttribute("data-group") || "").trim();
    if (!g) return;
    setGroupLocation(g, sel.value);
  });

  // CN
  document.querySelectorAll('#groups input[data-group]').forEach((inp) => {
    const g = String(inp.getAttribute("data-group") || "").trim();
    if (!g) return;
    setGroupCn(g, inp.value);
  });
}

/** ---------- Main actions ---------- */
async function loadAll() {
  if (!STATE.ov) {
    alert("Kein OV gesetzt.");
    return;
  }

  // 1) LDAP locations (dropdown source)
  const locPayload = await apiJson("/api/ldap/locations");
  STATE.ldapLocations = normalizeLocations(locPayload);

  // 2) groupmap for this OV
  const gmPayload = await apiJson(`/api/groupmap?ov=${encodeURIComponent(STATE.ov)}`);
  STATE.map = normalizeMap(gmPayload.map);

  // 3) discovery groups (server filters orgakuerzel == ov)
  const discPayload = await apiJson(`/api/groups?ov=${encodeURIComponent(STATE.ov)}&days=3650`);
  STATE.discoveryGroups = Array.isArray(discPayload.groups) ? discPayload.groups : [];

  renderNotify();
  renderLocationsBox(); // no-op if locsc missing
  renderGroupsTable();
}

async function saveAll() {
  if (!STATE.ov) {
    alert("Kein OV gesetzt.");
    return;
  }

  // <<< WICHTIG: sicherstellen, dass CN + Standort wirklich im STATE stehen
  syncGroupsFromDom();

  // ensure schema
  STATE.map = normalizeMap(STATE.map);

  // optional: drop empty group entries (no CN and no location)
  const cleanGroups = {};
  for (const [g, cfg] of Object.entries(STATE.map.groups || {})) {
    const adcn = String(cfg?.ad_cn || "").trim();
    const loc = String(cfg?.location || "").trim().toLowerCase();
    if (!adcn && !loc) continue;
    cleanGroups[g] = { ...cfg, location: loc, ad_cn: adcn };
  }
  STATE.map.groups = cleanGroups;

  await apiPostJson(`/api/groupmap?ov=${encodeURIComponent(STATE.ov)}`, { map: STATE.map });
  alert("✅ Gespeichert.");
}

async function runSync(mode) {
  const full = mode === "full" ? 1 : 0;
  try {
    await apiPostJson(
      `/api/sync/ad/run?ov=${encodeURIComponent(STATE.ov)}&full=${full}&dry_run=0`,
      {}
    );
    alert("✅ Sync gestartet.");
  } catch (e) {
    alert("Sync Fehler: " + (e?.message || e));
  }
}

// expose to HTML onclick
window.loadAll = loadAll;
window.saveAll = saveAll;
window.runSync = runSync;
window.addLoc = addLoc;
window.removeLoc = removeLoc;
window.setNotifyEnabled = setNotifyEnabled;
window.setNotify = setNotify;
window.setNotifyFreq = setNotifyFreq;
window.setGroupLocation = setGroupLocation;
window.setGroupCn = setGroupCn;

// Auto-load
loadAll().catch((err) => {
  console.error(err);
});

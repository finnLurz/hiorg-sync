// /static/groupmap.js

let groupMap = null;
let discovered = null;

const OV = window.HIORG_OV || "";

function apiHeaders() {
  return { "Content-Type": "application/json" };
}

function ensureNotify() {
  groupMap.notify = groupMap.notify || {};
  groupMap.notify.enabled = !!groupMap.notify.enabled;
  groupMap.notify.to = groupMap.notify.to || "";
  groupMap.notify.subject = groupMap.notify.subject || "";
  groupMap.notify.freq_hours = Number(groupMap.notify.freq_hours || 0);
}

function renderNotify() {
  ensureNotify();

  const elEnabled = document.getElementById("notify_enabled");
  const elTo = document.getElementById("notify_to");
  const elSubject = document.getElementById("notify_subject");
  const elFreq = document.getElementById("notify_freq");

  if (!elEnabled || !elTo || !elSubject || !elFreq) return;

  elEnabled.checked = !!groupMap.notify.enabled;
  elTo.value = groupMap.notify.to || "";
  elSubject.value = groupMap.notify.subject || "";
  elFreq.value = String(groupMap.notify.freq_hours || 0);
}

/**
 * Wichtig: Nutzer klickt oft direkt "Speichern" ohne Blur.
 * Daher IMMER vor Validierung/POST die Werte aus dem DOM ziehen.
 */
function readNotifyFromDom() {
  const elEnabled = document.getElementById("notify_enabled");
  const elTo = document.getElementById("notify_to");
  const elSubject = document.getElementById("notify_subject");
  const elFreq = document.getElementById("notify_freq");

  if (!elEnabled || !elTo || !elSubject || !elFreq) return;

  ensureNotify();
  groupMap.notify.enabled = !!elEnabled.checked;
  groupMap.notify.to = String(elTo.value || "").trim();
  groupMap.notify.subject = String(elSubject.value || "");
  const n = Number(elFreq.value || 0);
  groupMap.notify.freq_hours = Number.isFinite(n) ? n : 0;
}

async function loadAll() {
  const gm = await fetch(`/api/groupmap?ov=${encodeURIComponent(OV)}`, { headers: apiHeaders() });
  groupMap = (await gm.json()).map || {};

  const dg = await fetch(`/api/groups?ov=${encodeURIComponent(OV)}`, { headers: apiHeaders() });
  discovered = (await dg.json()).groups || [];

  groupMap.locations = groupMap.locations || {};
  groupMap.groups = groupMap.groups || {};
  ensureNotify();

  render();
  renderNotify();
}

function getLocationOptions() {
  const locs = Object.keys(groupMap.locations || {});
  locs.sort((a, b) => a.localeCompare(b));
  return ["Unbekannt", ...locs];
}

function baseDnFor(location) {
  if (!location || location === "Unbekannt") return "";
  return (groupMap.locations?.[location]?.base_dn) || "";
}

function ensureGroup(name) {
  groupMap.groups = groupMap.groups || {};
  groupMap.groups[name] = groupMap.groups[name] || {};
  // Migration: altes Feld base_dn entfernen
  if ("base_dn" in groupMap.groups[name]) delete groupMap.groups[name].base_dn;
  return groupMap.groups[name];
}

function render() {
  // --- Locations ---
  const locsDiv = document.getElementById("locs");
  if (locsDiv) {
    locsDiv.innerHTML = "";

    const locEntries = Object.entries(groupMap.locations || {});
    locEntries.sort((a, b) => a[0].localeCompare(b[0]));

    for (const [loc, cfg] of locEntries) {
      const base = (cfg && cfg.base_dn) ? cfg.base_dn : "";

      const div = document.createElement("div");
      div.className = "fwRow";
      div.innerHTML = `
        <input class="fwInput" value="${escapeHtml(loc)}" onchange="renameLoc('${jsStr(loc)}', this.value)">
        <div></div>
        <input class="fwInput" value="${escapeHtml(base)}"
              placeholder="OU=Gruppen,OU=Standorte,DC=feuerwehr,DC=de"
              onchange="setLocBase('${jsStr(loc)}', this.value)">
        <button class="fwBtn2" type="button" onclick="delLoc('${jsStr(loc)}')">Löschen</button>
      `;
      locsDiv.appendChild(div);
    }
  }

  // --- Groups ---
  const groupsDiv = document.getElementById("groups");
  if (!groupsDiv) return;

  groupsDiv.innerHTML = "";
  groupMap.groups = groupMap.groups || {};

  // Ensure all discovered groups exist
  for (const g of (discovered || [])) {
    const name = g.group;
    if (!name) continue;

    if (!groupMap.groups[name]) {
      const loc = (g.locations && g.locations[0]) ? g.locations[0] : "Unbekannt";
      groupMap.groups[name] = { location: loc, ad_cn: "" };
    } else {
      if ("base_dn" in groupMap.groups[name]) delete groupMap.groups[name].base_dn;
    }
  }

  const entries = Object.entries(groupMap.groups);
  entries.sort((a, b) => a[0].localeCompare(b[0]));

  const options = getLocationOptions();

  for (const [gname] of entries) {
    const rec = ensureGroup(gname);

    const loc = rec.location || "Unbekannt";
    const base_dn = baseDnFor(loc);
    const ad_cn = rec.ad_cn || "";

    const optsHtml = options.map(o => {
      const sel = (o === loc) ? "selected" : "";
      return `<option value="${escapeHtml(o)}" ${sel}>${escapeHtml(o)}</option>`;
    }).join("");

    const row = document.createElement("div");
    row.className = "fwRow";
    row.innerHTML = `
      <div style="font-weight:700;">${escapeHtml(gname)}</div>

      <select class="fwInput" onchange="setGroupLocation('${jsStr(gname)}', this.value)">
        ${optsHtml}
      </select>

      <input class="fwInput" value="${escapeHtml(base_dn)}"
            placeholder="(wird aus Standort gezogen)" readonly>

      <input class="fwInput" value="${escapeHtml(ad_cn)}"
            placeholder="CN in AD (Pflicht, sobald Standort gewählt)"
            onchange="setGroup('${jsStr(gname)}', 'ad_cn', this.value)">
    `;
    groupsDiv.appendChild(row);
  }
}

function addLoc() {
  const name = prompt("Standort-Name?");
  if (!name) return;
  groupMap.locations = groupMap.locations || {};
  if (!groupMap.locations[name]) groupMap.locations[name] = { base_dn: "" };
  render();
}

function renameLoc(oldName, newName) {
  if (!newName || newName === oldName) return;
  if (!groupMap.locations?.[oldName]) return;

  if (groupMap.locations[newName]) {
    alert("Der Standort existiert bereits: " + newName);
    return;
  }

  const cfg = groupMap.locations[oldName];
  delete groupMap.locations[oldName];
  groupMap.locations[newName] = cfg;

  for (const g in (groupMap.groups || {})) {
    if (groupMap.groups[g].location === oldName) groupMap.groups[g].location = newName;
  }
  render();
}

function setLocBase(loc, base) {
  groupMap.locations = groupMap.locations || {};
  groupMap.locations[loc] = groupMap.locations[loc] || {};
  groupMap.locations[loc].base_dn = base;
  render();
}

function delLoc(loc) {
  if (!confirm("Standort wirklich löschen?")) return;
  if (groupMap.locations) delete groupMap.locations[loc];

  for (const g in (groupMap.groups || {})) {
    if (groupMap.groups[g].location === loc) groupMap.groups[g].location = "Unbekannt";
  }
  render();
}

function setGroupLocation(g, loc) {
  const rec = ensureGroup(g);
  rec.location = loc;
  render();
}

function setGroup(g, k, v) {
  const rec = ensureGroup(g);
  rec[k] = v;
}

// ---- Notify setters ----
function setNotifyEnabled(enabled) {
  ensureNotify();
  groupMap.notify.enabled = !!enabled;
}
function setNotify(key, value) {
  ensureNotify();
  groupMap.notify[key] = String(value || "");
}
function setNotifyFreq(v) {
  ensureNotify();
  const n = Number(v || 0);
  groupMap.notify.freq_hours = Number.isFinite(n) ? n : 0;
}

window.setNotifyEnabled = setNotifyEnabled;
window.setNotify = setNotify;
window.setNotifyFreq = setNotifyFreq;

// ---- Save ----
async function saveAll() {
  // Wichtig: immer DOM -> groupMap vor dem POST ziehen
  readNotifyFromDom();

  // Migration cleanup
  for (const g in (groupMap.groups || {})) {
    if ("base_dn" in groupMap.groups[g]) delete groupMap.groups[g].base_dn;
  }
  ensureNotify();

  // CN Pflicht: wenn Standort != Unbekannt => ad_cn muss gesetzt sein
  const missing = [];
  for (const [gname, cfg] of Object.entries(groupMap.groups || {})) {
    const loc = (cfg.location || "Unbekannt");
    const cn = (cfg.ad_cn || "").trim();
    if (loc !== "Unbekannt" && !cn) missing.push(gname);
  }

  if (missing.length) {
    alert(
      "Speichern nicht möglich:\n" +
      "Für folgende Gruppen ist ein Standort gewählt, aber AD Gruppen-CN fehlt:\n\n" +
      missing.slice(0, 60).join("\n") +
      (missing.length > 60 ? `\n… (+${missing.length - 60} weitere)` : "")
    );
    return;
  }

  // Notify Validierung
  if (groupMap.notify.enabled) {
    const to = (groupMap.notify.to || "").trim();
    if (!to) {
      alert("Mailversand ist aktiv, aber Empfängeradresse ist leer.");
      return;
    }
  }

  const r = await fetch(`/api/groupmap?ov=${encodeURIComponent(OV)}`, {
    method: "POST",
    headers: apiHeaders(),
    body: JSON.stringify({ map: groupMap }),
  });

  const j = await r.json().catch(() => ({}));
  alert(j.ok ? "Gespeichert" : ("Fehler" + (j.error ? (": " + j.error) : "")));

  // optional: nach speichern wieder sauber rendern
  // (falls Server normalisiert)
  // await loadAll();
}

async function runSync(mode = "delta") {
  const full = (mode === "full");
  const dry = confirm("Dry-Run?\nOK = nur prüfen (keine Änderungen)\nAbbrechen = wirklich ausführen");

  const url = `/api/sync/ad/run?ov=${encodeURIComponent(OV)}&full=${full ? 1 : 0}&dry_run=${dry ? 1 : 0}`;

  const btns = Array.from(document.querySelectorAll("button")).filter(
    b => (b.onclick && String(b.onclick).includes("runSync"))
  );
  btns.forEach(b => b.disabled = true);

  const controller = new AbortController();
  const t = setTimeout(() => controller.abort(), 15000);

  try {
    const r = await fetch(url, { method: "POST", headers: apiHeaders(), signal: controller.signal });
    const j = await r.json().catch(() => ({}));
    clearTimeout(t);

    if (r.ok && j.ok) {
      alert(`Sync gestartet (${full ? "full" : "normal"}${dry ? ", dry-run" : ""}).`);
    } else {
      alert(`Sync-Fehler (${j.status || r.status}):\n${String(j.body || j.detail || r.statusText || "").slice(0, 1200)}`);
    }
  } catch (e) {
    clearTimeout(t);
    alert("Sync-Request abgebrochen/Timeout: " + e);
  } finally {
    btns.forEach(b => b.disabled = false);
  }
}

window.runSync = runSync;

// helpers
function escapeHtml(s) {
  return String(s)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}
function jsStr(s) {
  return String(s).replaceAll("\\", "\\\\").replaceAll("'", "\\'");
}

// expose
window.loadAll = loadAll;
window.saveAll = saveAll;
window.addLoc = addLoc;
window.renameLoc = renameLoc;
window.setLocBase = setLocBase;
window.delLoc = delLoc;
window.setGroup = setGroup;
window.setGroupLocation = setGroupLocation;

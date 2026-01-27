let groupMap = null;
let discovered = null;

const OV = window.HIORG_OV || "";

function apiHeaders() {
  return { "Content-Type": "application/json" };
}

async function loadAll() {
  const gm = await fetch(`/api/groupmap?ov=${encodeURIComponent(OV)}`, { headers: apiHeaders() });
  groupMap = (await gm.json()).map;

  const dg = await fetch(`/api/groups?ov=${encodeURIComponent(OV)}`, { headers: apiHeaders() });
  discovered = (await dg.json()).groups;

  render();
}

function render() {
  // locations
  const locs = document.getElementById("locs");
  locs.innerHTML = "";

  const locEntries = Object.entries(groupMap.locations || {});
  locEntries.sort((a, b) => a[0].localeCompare(b[0]));

  for (const [loc, cfg] of locEntries) {
    const base = (cfg && cfg.base_dn) ? cfg.base_dn : "";

    const div = document.createElement("div");
    div.className = "fwRow";
    div.innerHTML = `
      <input class="fwInput" value="${escapeHtml(loc)}" onchange="renameLoc('${jsStr(loc)}', this.value)">
      <div></div>
      <input class="fwInput" value="${escapeHtml(base)}" placeholder="OU=Gruppen,OU=Mitte,OU=Standorte,DC=fw-obu,DC=de" onchange="setLocBase('${jsStr(loc)}', this.value)">
      <button class="fwBtn2" type="button" onclick="delLoc('${jsStr(loc)}')">Löschen</button>
    `;
    locs.appendChild(div);
  }

  // groups
  const gdiv = document.getElementById("groups");
  gdiv.innerHTML = "";

  groupMap.groups = groupMap.groups || {};

  // initial: discovered groups -> ensure entry in groupMap.groups
  for (const g of (discovered || [])) {
    const name = g.group;
    if (!groupMap.groups[name]) {
      const loc = (g.locations && g.locations[0]) ? g.locations[0] : "Unbekannt";
      const base_dn = (groupMap.locations?.[loc]?.base_dn) || "";
      groupMap.groups[name] = { location: loc, base_dn: base_dn, ad_cn: "" };
    }
  }

  const entries = Object.entries(groupMap.groups);
  entries.sort((a, b) => a[0].localeCompare(b[0]));

  for (const [gname, cfg] of entries) {
    const loc = cfg.location || "Unbekannt";
    const base_dn = cfg.base_dn || (groupMap.locations?.[loc]?.base_dn) || "";
    const ad_cn = cfg.ad_cn || "";

    const row = document.createElement("div");
    row.className = "fwRow";
    row.innerHTML = `
      <div style="font-weight:700;">${escapeHtml(gname)}</div>
      <input class="fwInput" value="${escapeHtml(loc)}" onchange="setGroup('${jsStr(gname)}', 'location', this.value)">
      <input class="fwInput" value="${escapeHtml(base_dn)}" placeholder="GroupBaseDN" onchange="setGroup('${jsStr(gname)}', 'base_dn', this.value)">
      <input class="fwInput" value="${escapeHtml(ad_cn)}" placeholder="leer = CN=HiOrgName" onchange="setGroup('${jsStr(gname)}', 'ad_cn', this.value)">
    `;
    gdiv.appendChild(row);
  }
}

function addLoc() {
  const name = prompt("Standort-Name?");
  if (!name) return;
  groupMap.locations = groupMap.locations || {};
  groupMap.locations[name] = { base_dn: "" };
  render();
}

function renameLoc(oldName, newName) {
  if (!newName || newName === oldName) return;
  const cfg = groupMap.locations[oldName];
  delete groupMap.locations[oldName];
  groupMap.locations[newName] = cfg;

  // update group references
  for (const g in groupMap.groups) {
    if (groupMap.groups[g].location === oldName) groupMap.groups[g].location = newName;
  }
  render();
}

function setLocBase(loc, base) {
  groupMap.locations[loc] = groupMap.locations[loc] || {};
  groupMap.locations[loc].base_dn = base;
}

function delLoc(loc) {
  if (!confirm("Standort wirklich löschen?")) return;
  delete groupMap.locations[loc];
  render();
}

function setGroup(g, k, v) {
  groupMap.groups[g] = groupMap.groups[g] || {};
  groupMap.groups[g][k] = v;
}

async function saveAll() {
  const r = await fetch(`/api/groupmap?ov=${encodeURIComponent(OV)}`, {
    method: "POST",
    headers: apiHeaders(),
    body: JSON.stringify({ map: groupMap }),
  });
  const j = await r.json();
  alert(j.ok ? "Gespeichert" : "Fehler");
}

// helpers: verhindern XSS/kaputte Quotes in inline handlers
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

// expose functions for inline onclick handlers
window.loadAll = loadAll;
window.saveAll = saveAll;
window.addLoc = addLoc;
window.renameLoc = renameLoc;
window.setLocBase = setLocBase;
window.delLoc = delLoc;
window.setGroup = setGroup;

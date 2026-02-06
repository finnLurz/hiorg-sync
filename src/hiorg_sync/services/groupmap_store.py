# src/hiorg_sync/services/groupmap_store.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from ..core.settings import DATA_DIR
from .config_store import read_json, write_json_atomic, CONFIG_PATH

DATA_DIR_PATH = Path(DATA_DIR)
SETTINGS_DIR = DATA_DIR_PATH / "settings"
GROUPMAP_PATH = SETTINGS_DIR / "groupmap.json"


# -----------------------------
# Central config (config.json)
# schema:
# {
#   "base_dn_by_location": {
#     "bommersheim": "OU=Gruppen,OU=Bommersheim,OU=Standorte,DC=fw-obu,DC=de",
#     "mitte": "OU=Gruppen,OU=Mitte,OU=Standorte,DC=fw-obu,DC=de"
#   }
# }
# -----------------------------
def load_global_config() -> dict[str, Any]:
    cfg = read_json(CONFIG_PATH, default={})
    return cfg if isinstance(cfg, dict) else {}


def get_base_dn_for_location(location: str) -> str:
    """
    Strict:
    - returns "" if location is empty or not configured
    """
    loc = str(location or "").strip().lower()
    if not loc:
        return ""

    cfg = load_global_config()
    by_loc = cfg.get("base_dn_by_location") or {}
    if not isinstance(by_loc, dict):
        return ""

    # normalize lookup
    val = by_loc.get(loc)
    return str(val or "").strip()


# -----------------------------
# Groupmap load/save (GLOBAL)
# groupmap.json schema:
# {
#   "version": 2,
#   "locations": { "bommersheim": {}, "mitte": {} },
#   "groups": {
#      "Atemschutzgeräteträger": {"location":"bommersheim","ad_cn":"Bommersheim_Atemschutz"}
#   },
#   "notify": {...}   (wenn ihr das weiterhin nutzt)
# }
# -----------------------------
def _default_groupmap() -> dict[str, Any]:
    return {"version": 2, "locations": {}, "groups": {}, "notify": {}}


def _normalize_groupmap(m: Any) -> dict[str, Any]:
    if not isinstance(m, dict):
        m = {}

    m.setdefault("version", 2)
    m.setdefault("locations", {})
    m.setdefault("groups", {})
    m.setdefault("notify", {})

    if not isinstance(m.get("locations"), dict):
        m["locations"] = {}
    if not isinstance(m.get("groups"), dict):
        m["groups"] = {}
    if not isinstance(m.get("notify"), dict):
        m["notify"] = {}

    # normalize location keys (lowercase, stripped)
    locs_norm: dict[str, Any] = {}
    for k, v in (m.get("locations") or {}).items():
        kk = str(k).strip()
        if not kk:
            continue
        # Keys wie "Mitte" sollen stabil auf "mitte" gehen
        locs_norm[kk.lower()] = v if isinstance(v, dict) else {}
    m["locations"] = locs_norm

    # normalize groups entries (do NOT lowercase group names; HiOrg name is key)
    groups_norm: dict[str, Any] = {}
    for gname, cfg in (m.get("groups") or {}).items():
        gg = str(gname).strip()
        if not gg:
            continue
        cc = cfg if isinstance(cfg, dict) else {}
        # normalize location in each group to lowercase for stable join
        if "location" in cc:
            cc["location"] = str(cc.get("location") or "").strip().lower()
        groups_norm[gg] = cc
    m["groups"] = groups_norm

    return m


def load_groupmap() -> dict[str, Any]:
    if not GROUPMAP_PATH.exists():
        return _default_groupmap()
    try:
        raw = GROUPMAP_PATH.read_text(encoding="utf-8").strip()
        if not raw:
            return _default_groupmap()
        return _normalize_groupmap(json.loads(raw))
    except Exception:
        return _default_groupmap()


def save_groupmap(m: dict[str, Any]) -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    normalized = _normalize_groupmap(m)
    write_json_atomic(GROUPMAP_PATH, normalized)


# -----------------------------
# Helpers for resolving base_dn
# -----------------------------
def resolve_location_base_dn(location_key: str) -> str:
    """
    Strict:
    - Location comes from groupmap "locations" or group entry
    - BaseDN comes only from config.json (per location)
    - No defaults
    """
    loc = str(location_key or "").strip().lower()
    if not loc:
        return ""
    return get_base_dn_for_location(loc)


def resolve_group_base_dn(group_name: str) -> str:
    """
    Strict:
    - reads group->location from central groupmap.json
    - resolves via config.json base_dn_by_location
    - returns "" if missing
    """
    m = load_groupmap()
    groups = m.get("groups") or {}
    if not isinstance(groups, dict):
        return ""

    gcfg = groups.get(group_name) or {}
    if not isinstance(gcfg, dict):
        return ""

    loc = str(gcfg.get("location") or "").strip().lower()
    if not loc:
        return ""

    return resolve_location_base_dn(loc)

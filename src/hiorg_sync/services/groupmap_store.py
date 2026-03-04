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
# OV map (per OV slice)
# -----------------------------
def _default_ov_map() -> dict[str, Any]:
    return {"version": 2, "locations": {}, "groups": {}, "notify": {}}


def _normalize_ov_map(m: Any) -> dict[str, Any]:
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

    # locations: normalize keys to lowercase
    locs_norm: dict[str, Any] = {}
    for k, v in (m.get("locations") or {}).items():
        kk = str(k).strip().lower()
        if kk:
            locs_norm[kk] = v if isinstance(v, dict) else {}
    m["locations"] = locs_norm

    # groups: keep group name as-is, normalize location/ad_cn
    groups_norm: dict[str, Any] = {}
    for gname, cfg in (m.get("groups") or {}).items():
        gg = str(gname).strip()
        if not gg:
            continue
        cc = cfg if isinstance(cfg, dict) else {}
        if "location" in cc:
            cc["location"] = str(cc.get("location") or "").strip().lower()
        if "ad_cn" in cc:
            cc["ad_cn"] = str(cc.get("ad_cn") or "").strip()
        groups_norm[gg] = cc
    m["groups"] = groups_norm

    return m


def _load_all() -> dict[str, Any]:
    if not GROUPMAP_PATH.exists():
        return {"version": 3, "ovs": {}}

    try:
        raw = GROUPMAP_PATH.read_text(encoding="utf-8").strip()
        if not raw:
            return {"version": 3, "ovs": {}}

        d = json.loads(raw)
        if not isinstance(d, dict):
            return {"version": 3, "ovs": {}}

        d.setdefault("version", 3)
        d.setdefault("ovs", {})
        if not isinstance(d.get("ovs"), dict):
            d["ovs"] = {}

        ovs_norm: dict[str, Any] = {}
        for ov, ov_map in (d.get("ovs") or {}).items():
            ok = str(ov).strip().lower()
            if ok:
                ovs_norm[ok] = _normalize_ov_map(ov_map)
        d["ovs"] = ovs_norm
        return d
    except Exception:
        return {"version": 3, "ovs": {}}


def _save_all(d: dict[str, Any]) -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)
    write_json_atomic(GROUPMAP_PATH, d)


def load_groupmap(ov: str) -> dict[str, Any]:
    ovk = str(ov or "").strip().lower()
    if not ovk:
        return _default_ov_map()
    d = _load_all()
    ovs = d.get("ovs") or {}
    if not isinstance(ovs, dict):
        return _default_ov_map()
    return _normalize_ov_map(ovs.get(ovk) or _default_ov_map())


def save_groupmap(ov: str, m: dict[str, Any]) -> None:
    ovk = str(ov or "").strip().lower()
    if not ovk:
        return
    d = _load_all()
    d.setdefault("version", 3)
    d.setdefault("ovs", {})
    if not isinstance(d["ovs"], dict):
        d["ovs"] = {}
    d["ovs"][ovk] = _normalize_ov_map(m)
    _save_all(d)


# -----------------------------
# BaseDN (zentral) aus config.json
# -----------------------------
def load_global_config() -> dict[str, Any]:
    cfg = read_json(CONFIG_PATH, default={})
    return cfg if isinstance(cfg, dict) else {}


def get_location_map_from_config() -> dict[str, str]:
    """
    location_key -> base_dn aus config.json (base_dn_by_location)
    """
    cfg = load_global_config()
    by_loc = cfg.get("base_dn_by_location") or {}
    if not isinstance(by_loc, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in by_loc.items():
        kk = str(k).strip().lower()
        vv = str(v).strip()
        if kk and vv:
            out[kk] = vv
    return out


def list_locations_from_config() -> list[dict[str, str]]:
    """
    Für UI: Liste von {key, base_dn}
    """
    m = get_location_map_from_config()
    return [{"key": k, "base_dn": m[k]} for k in sorted(m.keys())]


def resolve_location_base_dn(location_key: str) -> str:
    loc = str(location_key or "").strip().lower()
    if not loc:
        return ""
    return get_location_map_from_config().get(loc, "").strip()


def resolve_group_base_dn(ov: str, group_name: str) -> str:
    m = load_groupmap(ov)
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

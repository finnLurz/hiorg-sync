# src/hiorg_sync/services/ldap_config.py
from __future__ import annotations

from typing import Any

from .config_store import read_json, CONFIG_PATH


def load_ldap_config() -> dict[str, Any]:
    """
    Reads DATA_DIR/settings/config.json.

    Expected schema:
    {
      "base_dn_by_location": {
        "<location-key>": "<BaseDN string>",
        ...
      }
    }

    Notes:
    - No defaults
    - No environment overrides
    - Keys are normalized to lower-case + stripped
    """
    cfg = read_json(CONFIG_PATH, default={})
    if not isinstance(cfg, dict):
        cfg = {}

    raw = cfg.get("base_dn_by_location")
    if not isinstance(raw, dict):
        raw = {}

    out: dict[str, str] = {}
    for k, v in raw.items():
        kk = str(k).strip().lower()
        vv = str(v).strip()
        if kk and vv:
            out[kk] = vv

    return {"base_dn_by_location": out}


def resolve_base_dn_for_location(location: str) -> str:
    """
    Strict: returns "" if location is missing or not configured.
    """
    loc = str(location or "").strip().lower()
    if not loc:
        return ""

    cfg = load_ldap_config()
    by_loc = cfg.get("base_dn_by_location")
    if not isinstance(by_loc, dict):
        return ""

    return str(by_loc.get(loc, "") or "").strip()

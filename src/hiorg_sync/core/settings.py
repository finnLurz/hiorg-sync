from __future__ import annotations

import os
from typing import Any

from fastapi import HTTPException

from ..services.config_store import read_json, CONFIG_PATH, LDAP_PATH  # <-- LDAP_PATH dazu


# ---------------------------------------------------------
# LDAP JSON fallback: ENV wins
# ---------------------------------------------------------
_ldap_cfg = read_json(LDAP_PATH, default={})
if not isinstance(_ldap_cfg, dict):
    _ldap_cfg = {}

def _env_has(key: str) -> bool:
    v = os.getenv(key)
    return v is not None and str(v).strip() != ""

def _get_raw(key: str, default: Any = None) -> Any:
    if _env_has(key):
        return os.getenv(key)
    return _ldap_cfg.get(key, default)

def _get_str(key: str, default: str = "") -> str:
    v = _get_raw(key, default)
    if v is None:
        return default
    return str(v).strip()

def _get_bool(key: str, default: bool = False) -> bool:
    v = _get_raw(key, default)
    if isinstance(v, bool):
        return v
    if v is None:
        return default
    s = str(v).strip().lower()
    if s == "":
        return default
    return s in ("1", "true", "yes", "on", "y", "ja")

def _get_csv_set(key: str, default_csv: str = "") -> set[str]:
    """
    Accepts:
      - JSON: ["a","b"] or "a,b"
      - ENV:  "a,b" (or with spaces/newlines)
    Returns lowercased set.
    """
    v = _get_raw(key, default_csv)
    items: list[str] = []

    if isinstance(v, list):
        items = [str(x) for x in v]
    else:
        s = str(v or "")
        s = s.replace("\n", ",").replace(" ", ",")
        items = s.split(",")

    return {x.strip().lower() for x in items if str(x).strip()}


# ------------------------------------------------------------
# OV
# ------------------------------------------------------------
def _parse_ov_list(raw: str) -> list[str]:
    """
    Split by comma or newline, trim, lowercase, unique (keep order).
    """
    raw = (raw or "").strip()
    if not raw:
        return []

    raw = raw.replace("\n", ",")
    seen: set[str] = set()
    out: list[str] = []
    for part in raw.split(","):
        ov = part.strip().lower()
        if not ov or ov in seen:
            continue
        seen.add(ov)
        out.append(ov)
    return out


def get_ov_list() -> list[str]:
    """
    OV list:
    1) If env var OV_LIST is set (non-empty) => use it (override).
    2) Else: load from DATA_DIR/settings/config.json key "ov_list"
       - supports list[str] or str
    """
    # 1) ENV override
    env_raw = os.environ.get("OV_LIST")
    if env_raw is not None and env_raw.strip():
        return _parse_ov_list(env_raw)

    # 2) UI config (config.json)
    cfg = read_json(CONFIG_PATH, default={})
    if not isinstance(cfg, dict):
        return []

    v = cfg.get("ov_list")
    if isinstance(v, list):
        return _parse_ov_list(",".join([str(x) for x in v]))
    if isinstance(v, str):
        return _parse_ov_list(v)

    return []


def require_ov(ov: str) -> str:
    """
    Validate ov and return normalized (lowercase) value.
    """
    ov_n = (ov or "").strip().lower()
    if not ov_n:
        raise HTTPException(400, "Missing ov parameter")

    ovs = get_ov_list()
    if ovs and ov_n not in ovs:
        raise HTTPException(403, f"OV '{ov_n}' not allowed (allowed: {', '.join(ovs)})")

    return ov_n


# ------------------------------------------------------------
# App / Data
# ------------------------------------------------------------
APP_NAME = os.getenv("APP_NAME", "hiorg-sync")
DATA_DIR = os.getenv("DATA_DIR", "/var/lib/hiorg-sync")
INITIAL_SYNC_DAYS = int(os.getenv("INITIAL_SYNC_DAYS", "365"))

# Optional: API Key for curl/scripts
SYNC_API_KEY = os.getenv("SYNC_API_KEY", "")

# ------------------------------------------------------------
# UI
# ------------------------------------------------------------
UI_PASSWORD = os.getenv("UI_PASSWORD", "")  # if empty -> UI unprotected
STATE_SECRET = os.getenv("STATE_SECRET", "change-me")
UI_SESSION_SECRET = os.getenv("UI_SESSION_SECRET", STATE_SECRET)
UI_SESSION_TTL_HOURS = int(os.getenv("UI_SESSION_TTL_HOURS", "12"))

# ------------------------------------------------------------
# HiOrg OAuth / API
# ------------------------------------------------------------
HIORG_CLIENT_ID = os.getenv("HIORG_CLIENT_ID", "")
HIORG_CLIENT_SECRET = os.getenv("HIORG_CLIENT_SECRET", "")
HIORG_REDIRECT_URI = os.getenv("HIORG_REDIRECT_URI", "")

HIORG_AUTH_URL = os.getenv("HIORG_AUTH_URL", "https://api.hiorg-server.de/oauth/v1/authorize")
HIORG_TOKEN_URL = os.getenv("HIORG_TOKEN_URL", "https://api.hiorg-server.de/oauth/v1/token")
HIORG_API_BASE = os.getenv("HIORG_API_BASE", "https://api.hiorg-server.de/core/v1")

# Scopes (no personal:put)
HIORG_SCOPE = os.getenv("HIORG_SCOPE", "openid personal:read personal:add personal:update")

# ------------------------------------------------------------
# LDAP / AD  (ENV wins, ldap.json fallback)
# ------------------------------------------------------------
LDAP_URL = _get_str("LDAP_URL", "")
LDAP_BIND_USER = _get_str("LDAP_BIND_USER", "")
LDAP_BIND_PASSWORD = _get_str("LDAP_BIND_PASSWORD", "")
LDAP_DEFAULT_DOMAIN = _get_str("LDAP_DEFAULT_DOMAIN", "fw.de")

# wichtig: du hast es in ldap.json, aber bisher nicht in settings.py
SYNC_AD_URL = _get_str("SYNC_AD_URL", "")

# OV -> OU mapping as JSON string (ENV override optional)
LDAP_OU_MAP_JSON = _get_str("LDAP_OU_MAP_JSON", _get_str("LDAP_OU_MAP", "")).strip()

# HiOrg ID mapping attribute (for Nextcloud, etc.)
LDAP_HIORG_ID_ATTR = _get_str("LDAP_HIORG_ID_ATTR", "msDS-cloudExtensionAttribute1")
LDAP_HIORG_ID_PREFIX = _get_str("LDAP_HIORG_ID_PREFIX", "hiorg-")

LDAP_OVERWRITE_EMPTY = _get_bool("LDAP_OVERWRITE_EMPTY", False)
LDAP_ONLY_STATUS_ACTIVE = _get_bool("LDAP_ONLY_STATUS_ACTIVE", True)

EXCLUDE_ORGAKUERZEL = _get_csv_set("EXCLUDE_ORGAKUERZEL", "stab")

LDAP_CREATE_ENABLED = _get_bool("LDAP_CREATE_ENABLED", False)
LDAP_MOVE_IF_OU_CHANGED = _get_bool("LDAP_MOVE_IF_OU_CHANGED", True)

LDAP_SAM_MODE = _get_str("LDAP_SAM_MODE", "hiorg_username").lower()
LDAP_SAM_USERNAME_KEY = _get_str("LDAP_SAM_USERNAME_KEY", "username")
LDAP_UPDATE_SAM = _get_bool("LDAP_UPDATE_SAM", False)

# Group sync
LDAP_GROUP_MEMBER_ATTR = _get_str("LDAP_GROUP_MEMBER_ATTR", "member")
LDAP_GROUP_SYNC_REMOVE = _get_bool("LDAP_GROUP_SYNC_REMOVE", False)

# HiOrg groups discovery / parsing
HIORG_LOCATION_KEY = _get_str("HIORG_LOCATION_KEY", "standort")
HIORG_GROUP_SPLIT_RE = _get_str("HIORG_GROUP_SPLIT_RE", r"\s*::\s*")

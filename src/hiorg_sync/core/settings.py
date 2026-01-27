from __future__ import annotations
import os
from fastapi import HTTPException

# ------------------------------------------------------------
# OV
# ------------------------------------------------------------
def get_ov_list() -> list[str]:
    """
    OV list comes from env var OV_LIST.
    - split by comma
    - trim spaces
    - lower-case
    - remove empty and duplicates (keep order)
    """
    raw = os.environ.get("OV_LIST", "").strip()
    if not raw:
        return []

    seen: set[str] = set()
    out: list[str] = []
    for part in raw.split(","):
        ov = part.strip().lower()
        if not ov or ov in seen:
            continue
        seen.add(ov)
        out.append(ov)
    return out


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
# LDAP / AD
# ------------------------------------------------------------
LDAP_URL = os.getenv("LDAP_URL", "")
LDAP_BIND_USER = os.getenv("LDAP_BIND_USER", "")  # recommendation: UPN, e.g. account@domain.tld
LDAP_BIND_PASSWORD = os.getenv("LDAP_BIND_PASSWORD", "")
LDAP_DEFAULT_DOMAIN = os.getenv("LDAP_DEFAULT_DOMAIN", "fw-obu.de")

# OV -> OU mapping as JSON string
LDAP_OU_MAP_JSON = os.getenv("LDAP_OU_MAP", "{}")

# HiOrg ID mapping attribute (for Nextcloud, etc.)
LDAP_HIORG_ID_ATTR = os.getenv("LDAP_HIORG_ID_ATTR", "msDS-cloudExtensionAttribute1")
LDAP_HIORG_ID_PREFIX = os.getenv("LDAP_HIORG_ID_PREFIX", "hiorg-")

LDAP_OVERWRITE_EMPTY = os.getenv("LDAP_OVERWRITE_EMPTY", "false").lower() in ("1", "true", "yes")
LDAP_ONLY_STATUS_ACTIVE = os.getenv("LDAP_ONLY_STATUS_ACTIVE", "true").lower() in ("1", "true", "yes")

EXCLUDE_ORGAKUERZEL = {
    x.strip().lower()
    for x in os.getenv("EXCLUDE_ORGAKUERZEL", "stab04").split(",")
    if x.strip()
}

LDAP_CREATE_ENABLED = os.getenv("LDAP_CREATE_ENABLED", "false").lower() in ("1", "true", "yes")
LDAP_MOVE_IF_OU_CHANGED = os.getenv("LDAP_MOVE_IF_OU_CHANGED", "true").lower() in ("1", "true", "yes")

LDAP_SAM_MODE = os.getenv("LDAP_SAM_MODE", "hiorg_username").lower()
LDAP_SAM_USERNAME_KEY = os.getenv("LDAP_SAM_USERNAME_KEY", "username")
LDAP_UPDATE_SAM = os.getenv("LDAP_UPDATE_SAM", "false").lower() in ("1", "true", "yes")

# Group sync
LDAP_GROUP_MEMBER_ATTR = os.getenv("LDAP_GROUP_MEMBER_ATTR", "member")
LDAP_GROUP_SYNC_REMOVE = os.getenv("LDAP_GROUP_SYNC_REMOVE", "false").lower() in ("1", "true", "yes")

# HiOrg groups discovery / parsing
HIORG_LOCATION_KEY = os.getenv("HIORG_LOCATION_KEY", "standort")
HIORG_GROUP_SPLIT_RE = os.getenv("HIORG_GROUP_SPLIT_RE", r"\s*::\s*")



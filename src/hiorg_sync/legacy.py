import os
import json
import time
import secrets
import hashlib
import re
import unicodedata
import base64
import hmac

from datetime import datetime, timezone, timedelta
from pathlib import Path
from urllib.parse import urlencode
from typing import Any

import requests
from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse, HTMLResponse

from ldap3 import Server, Connection, MODIFY_REPLACE, MODIFY_ADD, MODIFY_DELETE, SUBTREE, BASE
from ldap3.utils.dn import escape_rdn



APP_NAME = "hiorg-sync"

# -----------------------------
# HiOrg OAuth / API
# -----------------------------
HIORG_CLIENT_ID = os.getenv("HIORG_CLIENT_ID", "")
HIORG_CLIENT_SECRET = os.getenv("HIORG_CLIENT_SECRET", "")
HIORG_REDIRECT_URI = os.getenv("HIORG_REDIRECT_URI", "")

HIORG_AUTH_URL = os.getenv("HIORG_AUTH_URL", "https://api.hiorg-server.de/oauth/v1/authorize")
HIORG_TOKEN_URL = os.getenv("HIORG_TOKEN_URL", "https://api.hiorg-server.de/oauth/v1/token")
HIORG_API_BASE = os.getenv("HIORG_API_BASE", "https://api.hiorg-server.de/core/v1")

# Scopes (ohne personal:put)
HIORG_SCOPE = os.getenv("HIORG_SCOPE", "openid personal:read personal:add personal:update")

STATE_SECRET = os.getenv("STATE_SECRET", "change-me")

DATA_DIR = Path(os.getenv("DATA_DIR", "/var/lib/hiorg-sync"))
OV_LIST = [x.strip() for x in os.getenv("OV_LIST", "").split(",") if x.strip()]
INITIAL_SYNC_DAYS = int(os.getenv("INITIAL_SYNC_DAYS", "365"))
SYNC_API_KEY = os.getenv("SYNC_API_KEY", "")  # optional

UI_PASSWORD = os.getenv("UI_PASSWORD", "")  # wenn leer: UI ungeschützt
UI_SESSION_SECRET = os.getenv("UI_SESSION_SECRET", STATE_SECRET)
UI_SESSION_TTL_HOURS = int(os.getenv("UI_SESSION_TTL_HOURS", "12"))

# -----------------------------
# LDAP / AD
# -----------------------------
LDAP_URL = os.getenv("LDAP_URL", "")
LDAP_BIND_USER = os.getenv("LDAP_BIND_USER", "")  # empfehlung: UPN, z.B. sAccount.HiOrg@fw-obu.de
LDAP_BIND_PASSWORD = os.getenv("LDAP_BIND_PASSWORD", "")
LDAP_DEFAULT_DOMAIN = os.getenv("LDAP_DEFAULT_DOMAIN", "fw-obu.de")

# OV -> OU Mapping als JSON
LDAP_OU_MAP_JSON = os.getenv("LDAP_OU_MAP", "{}")

# HiOrg ID Ablage für Nextcloud mapping
LDAP_HIORG_ID_ATTR = os.getenv("LDAP_HIORG_ID_ATTR", "msDS-cloudExtensionAttribute1")   # z.B. employeeID
LDAP_HIORG_ID_PREFIX = os.getenv("LDAP_HIORG_ID_PREFIX", "hiorg-")   # "hiorg-"

# Verhalten bei leeren Werten:
# false = überschreibt NICHT mit leer (empfohlen fürs Telefonbuch)
LDAP_OVERWRITE_EMPTY = os.getenv("LDAP_OVERWRITE_EMPTY", "false").lower() in ("1", "true", "yes")

# Optional: nur aktive User übernehmen
LDAP_ONLY_STATUS_ACTIVE = os.getenv("LDAP_ONLY_STATUS_ACTIVE", "true").lower() in ("1", "true", "yes")

# Stab04 rausfiltern
EXCLUDE_ORGAKUERZEL = {x.strip().lower() for x in os.getenv("EXCLUDE_ORGAKUERZEL", "stab04").split(",") if x.strip()}

# AD User create Optionen
LDAP_CREATE_ENABLED = os.getenv("LDAP_CREATE_ENABLED", "false").lower() in ("1", "true", "yes")
LDAP_MOVE_IF_OU_CHANGED = os.getenv("LDAP_MOVE_IF_OU_CHANGED", "true").lower() in ("1", "true", "yes")
LDAP_SAM_MODE = os.getenv("LDAP_SAM_MODE", "hiorg_username").lower()
LDAP_SAM_USERNAME_KEY = os.getenv("LDAP_SAM_USERNAME_KEY", "username")
LDAP_UPDATE_SAM = os.getenv("LDAP_UPDATE_SAM", "false").lower() in ("1", "true", "yes")


DATA_DIR.mkdir(parents=True, exist_ok=True)

app = FastAPI(title=APP_NAME)


# -----------------------------
# helpers
# -----------------------------

# --- Gruppen-Mapping + Discovery helpers ---
HIORG_LOCATION_KEY = os.getenv("HIORG_LOCATION_KEY", "standort")
HIORG_GROUP_SPLIT_RE = os.getenv("HIORG_GROUP_SPLIT_RE", r"\s*::\s*")



def _person_location(attrs: dict) -> str:
    v = (attrs.get(HIORG_LOCATION_KEY) or "").strip()
    return v

def _split_group_location(group_name: str) -> tuple[str, str]:
    parts = re.split(HIORG_GROUP_SPLIT_RE, group_name, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "", group_name.strip()

def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ov_dir(ov: str) -> Path:
    d = DATA_DIR / ov
    d.mkdir(parents=True, exist_ok=True)
    return d
    
def _groupmap_path(ov: str) -> Path:
    return _ov_dir(ov) / "groupmap.json"

def _load_groupmap(ov: str) -> dict:
    p = _groupmap_path(ov)
    if not p.exists():
        return {
            "version": 1,
            "locations": {},
            "groups": {},
        }
    try:
        return json.loads(p.read_text())
    except Exception:
        return {"version": 1, "locations": {}, "groups": {}}

def _save_groupmap(ov: str, m: dict) -> None:
    _groupmap_path(ov).write_text(json.dumps(m, indent=2, ensure_ascii=False))

def _tokens_path(ov: str) -> Path:
    return _ov_dir(ov) / "tokens.json"


def _marker_path(ov: str) -> Path:
    return _ov_dir(ov) / "updated_since.txt"


def _state_path() -> Path:
    return DATA_DIR / "states.json"


def _load_states() -> dict:
    p = _state_path()
    if p.exists():
        try:
            return json.loads(p.read_text())
        except Exception:
            return {}
    return {}


def _save_states(states: dict) -> None:
    _state_path().write_text(json.dumps(states, indent=2))


def _require_ov(ov: str) -> None:
    if not ov:
        raise HTTPException(400, "Missing ov parameter")
    if OV_LIST and ov not in OV_LIST:
        raise HTTPException(403, f"OV '{ov}' not allowed (allowed: {', '.join(OV_LIST)})")


def _require_api_key(request: Request) -> None:
    if not SYNC_API_KEY:
        return
    got = request.headers.get("X-API-Key", "")
    if got != SYNC_API_KEY:
        raise HTTPException(401, "Missing/invalid X-API-Key")

def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("utf-8").rstrip("=")

def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)

def _ui_make_session() -> str:
    payload = {"ts": int(time.time()), "rnd": secrets.token_urlsafe(8)}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = _b64url_encode(raw).encode("utf-8")
    sig = hmac.new(UI_SESSION_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"{body.decode('utf-8')}.{sig}"

def _ui_verify_session(token: str) -> bool:
    if not token or "." not in token:
        return False
    body_s, sig = token.split(".", 1)
    body = body_s.encode("utf-8")
    expected = hmac.new(UI_SESSION_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, sig):
        return False
    try:
        payload = json.loads(_b64url_decode(body_s))
        ts = int(payload.get("ts", 0))
    except Exception:
        return False
    return (time.time() - ts) <= (UI_SESSION_TTL_HOURS * 3600)

def _require_ui_login(request: Request) -> None:
    # wenn kein UI_PASSWORD gesetzt -> offen lassen
    if not UI_PASSWORD:
        return
    token = request.cookies.get("ui_session", "")
    if not _ui_verify_session(token):
        raise HTTPException(401, "UI login required")

def _require_api_or_ui(request: Request) -> None:
    # 1) API-Key erlaubt (für curl/scripts)
    if SYNC_API_KEY:
        got = request.headers.get("X-API-Key", "")
        if got == SYNC_API_KEY:
            return
    # 2) sonst UI-Session
    _require_ui_login(request)


def _load_tokens(ov: str) -> dict:
    p = _tokens_path(ov)
    if not p.exists():
        raise HTTPException(412, f"No tokens stored for ov '{ov}'. Run /oauth/start?ov=... first.")
    return json.loads(p.read_text())


def _save_tokens(ov: str, tokens: dict) -> None:
    _tokens_path(ov).write_text(json.dumps(tokens, indent=2))


def _get_marker(ov: str) -> str:
    p = _marker_path(ov)
    if p.exists():
        v = p.read_text().strip()
        if v:
            return v
    return _iso(_now_utc() - timedelta(days=INITIAL_SYNC_DAYS))


def _set_marker(ov: str, marker: str) -> None:
    _marker_path(ov).write_text(marker + "\n")


def _gen_state(ov: str) -> str:
    ts = str(int(time.time()))
    rnd = secrets.token_urlsafe(16)
    raw = f"{STATE_SECRET}|{ov}|{ts}|{rnd}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _token_request(payload: dict) -> dict:
    r = requests.post(HIORG_TOKEN_URL, data=payload, timeout=30)
    if r.status_code >= 400:
        raise HTTPException(r.status_code, f"Token request failed: {r.text}")
    return r.json()


def _refresh_tokens(ov: str) -> dict:
    tokens = _load_tokens(ov)
    refresh = tokens.get("refresh_token")
    if not refresh:
        raise HTTPException(412, f"No refresh_token stored for ov '{ov}'")
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": HIORG_CLIENT_ID,
        "client_secret": HIORG_CLIENT_SECRET,
    }
    new_tokens = _token_request(payload)
    if "refresh_token" not in new_tokens:
        new_tokens["refresh_token"] = refresh
    _save_tokens(ov, new_tokens)
    return new_tokens


def _api_get(access_token: str, path: str, params: dict | None = None, url_override: str | None = None) -> dict:
    url = url_override or f"{HIORG_API_BASE}{path}"
    headers = {"Authorization": f"Bearer {access_token}", "Accept": "application/vnd.api+json"}
    r = requests.get(url, headers=headers, params=params, timeout=60)
    if r.status_code >= 400:
        raise HTTPException(r.status_code, f"API GET failed: {r.text}")
    return r.json()


def _fetch_personal_updated_since(access_token: str, updated_since: str) -> list[dict]:
    params = {"filter[updated_since]": updated_since}
    data_all: list[dict] = []

    first = _api_get(access_token, "/personal", params=params)
    if isinstance(first, dict) and isinstance(first.get("data"), list):
        data_all.extend(first["data"])

    next_url = None
    links = first.get("links") or {}
    next_url = links.get("next")

    def _abs(url: str) -> str:
        if url.startswith("http://") or url.startswith("https://"):
            return url
        return f"{HIORG_API_BASE}{url}"

    while next_url:
        page = _api_get(access_token, "/personal", url_override=_abs(next_url))
        if isinstance(page, dict) and isinstance(page.get("data"), list):
            data_all.extend(page["data"])
        links = page.get("links") or {}
        next_url = links.get("next")

    return data_all


# -----------------------------
# LDAP helpers
# -----------------------------
def _ldap_parse_url(url: str) -> tuple[str, int, bool]:
    if not url:
        raise HTTPException(500, "LDAP_URL missing")
    use_ssl = url.lower().startswith("ldaps://")
    hostport = url.replace("ldaps://", "").replace("ldap://", "")
    if "/" in hostport:
        hostport = hostport.split("/", 1)[0]
    if ":" in hostport:
        host, port_s = hostport.split(":", 1)
        return host, int(port_s), use_ssl
    return hostport, (636 if use_ssl else 389), use_ssl


def _ldap_conn() -> Connection:
    if not (LDAP_URL and LDAP_BIND_USER and LDAP_BIND_PASSWORD):
        raise HTTPException(500, "LDAP_URL / LDAP_BIND_USER / LDAP_BIND_PASSWORD missing")
    host, port, use_ssl = _ldap_parse_url(LDAP_URL)
    server = Server(host, port=port, use_ssl=use_ssl, get_info=None)
    conn = Connection(server, user=LDAP_BIND_USER, password=LDAP_BIND_PASSWORD, auto_bind=True)
    return conn


def _load_ou_map() -> dict[str, str]:
    try:
        m = json.loads(LDAP_OU_MAP_JSON or "{}")
        if not isinstance(m, dict):
            return {}
        return {str(k).lower(): str(v) for k, v in m.items()}
    except Exception:
        return {}


def _normalize_ascii(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    # Umlaute / ß vorher "schön" machen
    s = (s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
           .replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue")
           .replace("ß", "ss"))
    # dann unicode -> ascii
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return s


def _clean_sam_piece(s: str) -> str:
    s = _normalize_ascii(s).lower()
    s = re.sub(r"[^a-z0-9.]+", ".", s)
    s = re.sub(r"\.+", ".", s).strip(".")
    return s


def _sam_base(first: str, last: str) -> str:
    f = _clean_sam_piece(first)
    l = _clean_sam_piece(last)
    if f and l:
        return f"{f}.{l}"
    return (f or l or "user")


def _sam_short(base: str) -> str:
    # AD sAMAccountName praktisch max 20
    if len(base) <= 20:
        return base
    # versuch: erster Buchstabe Vorname + "." + Nachname
    parts = base.split(".")
    if len(parts) >= 2:
        f0 = parts[0][:1]
        l = ".".join(parts[1:])
        cand = f"{f0}.{l}"
        if len(cand) <= 20:
            return cand
        # Nachname kürzen
        l2 = l[: max(1, 20 - 2)]  # 1 + '.' + rest
        cand2 = f"{f0}.{l2}"
        return cand2[:20]
    return base[:20]

def _sam_base_from_person(attrs: dict) -> tuple[str, str]:
    """
    Returns (base_sam, fallback_username_raw)
    base_sam wird nach LDAP_SAM_MODE gebaut (noch NICHT unique geprüft).
    """
    h_username = str(attrs.get(LDAP_SAM_USERNAME_KEY, "") or "").strip()

    if LDAP_SAM_MODE in ("hiorg", "hiorg_username", "username"):
        base = _clean_sam_piece(h_username)
        if not base:
            base = _sam_base(str(attrs.get("vorname", "") or ""), str(attrs.get("nachname", "") or ""))
    else:
        base = _sam_base(str(attrs.get("vorname", "") or ""), str(attrs.get("nachname", "") or ""))

    return base, h_username


def _ldap_search_one(conn: Connection, base_dn: str, flt: str, attrs: list[str]) -> dict | None:
    ok = conn.search(search_base=base_dn, search_filter=flt, search_scope=SUBTREE, attributes=attrs, size_limit=1)
    if not ok or not conn.entries:
        return None
    e = conn.entries[0]
    return {"dn": e.entry_dn, "attributes": e.entry_attributes_as_dict}


def _ldap_attr_set(changes: dict, attr: str, value: str | None) -> None:
    if value is None:
        return
    value = str(value).strip()
    if value == "" and not LDAP_OVERWRITE_EMPTY:
        return
    if value == "":
        changes[attr] = [(MODIFY_REPLACE, [])]
    else:
        changes[attr] = [(MODIFY_REPLACE, [value])]


def _hiorg_attr(person: dict, key: str, default: str = "") -> str:
    a = person.get("attributes") or {}
    v = a.get(key, default)
    if v is None:
        return ""
    if isinstance(v, str):
        return v
    return str(v)


def _hiorg_groups(person: dict) -> list[str]:
    a = person.get("attributes") or {}
    g = a.get("gruppen_namen")
    if isinstance(g, list):
        return [str(x) for x in g]
    return []


def _build_hiorg_id(person: dict) -> str:
    pid = str(person.get("id", "")).strip()
    return f"{LDAP_HIORG_ID_PREFIX}{pid}" if pid else ""


def _map_person_to_ad_attrs(person: dict, sam: str) -> dict[str, Any]:
    first = _hiorg_attr(person, "vorname")
    last = _hiorg_attr(person, "nachname")
    display = _hiorg_attr(person, "name") or f"{first} {last}".strip()
    email = _hiorg_attr(person, "email")
    teldienst = _hiorg_attr(person, "teldienst")
    telpriv = _hiorg_attr(person, "telpriv")
    mobile = _hiorg_attr(person, "handy")
    street = _hiorg_attr(person, "adresse")
    plz = _hiorg_attr(person, "plz")
    city = _hiorg_attr(person, "ort")
    land = _hiorg_attr(person, "land")
    ov = _hiorg_attr(person, "orgakuerzel").lower()

    upn = f"{sam}@{LDAP_DEFAULT_DOMAIN}"

    attrs: dict[str, Any] = {
        "objectClass": ["top", "person", "organizationalPerson", "user"],
        "cn": display,
        "givenName": first,
        "sn": (last or display),
        "displayName": display,
        "sAMAccountName": sam,
        "userPrincipalName": upn,
        # Beschreibung: OV + Quelle
        "description": f"HiOrg {ov}",
        # Account disabled/enabled (ohne Passwort ist enabled oft unschön -> default disabled)
        "userAccountControl": 512 if LDAP_CREATE_ENABLED else 514,
    }

    # Nextcloud mapping key
    hid = _build_hiorg_id(person)
    if hid:
        attrs[LDAP_HIORG_ID_ATTR] = hid

    # Kontakt / Telefonbuch
    if email:
        attrs["mail"] = email
    if teldienst:
        attrs["telephoneNumber"] = teldienst
    if telpriv:
        attrs["homePhone"] = telpriv
    if mobile:
        attrs["mobile"] = mobile

    # Adresse
    if street:
        attrs["streetAddress"] = street
    if plz:
        attrs["postalCode"] = plz
    if city:
        attrs["l"] = city  # ja: Attribut heißt "l"
    if land:
        attrs["co"] = land

    return attrs


def _ensure_unique_sam(conn: Connection, search_base: str, base_sam: str, fallback_username: str) -> str:
    base_sam = _sam_short(base_sam)
    # wenn frei -> ok
    flt = f"(sAMAccountName={base_sam})"
    if not _ldap_search_one(conn, search_base, flt, ["sAMAccountName"]):
        return base_sam

    # Zählschleife: name2, name3, ...
    for i in range(2, 1000):
        suffix = str(i)
        cut = 20 - len(suffix)
        cand = (base_sam[:cut] + suffix)[:20]
        flt2 = f"(sAMAccountName={cand})"
        if not _ldap_search_one(conn, search_base, flt2, ["sAMAccountName"]):
            return cand

    # fallback: HiOrg username
    fb = _clean_sam_piece(fallback_username) or "user"
    fb = _sam_short(fb)
    if not _ldap_search_one(conn, search_base, f"(sAMAccountName={fb})", ["sAMAccountName"]):
        return fb

    # letzte Rettung: random
    rnd = ("u" + secrets.token_hex(8))[:20]
    return rnd


def _find_existing_by_hiorg_id(conn: Connection, search_base: str, hiorg_id: str) -> dict | None:
    if not hiorg_id:
        return None
    # exact match
    flt = f"({LDAP_HIORG_ID_ATTR}={hiorg_id})"
    return _ldap_search_one(conn, search_base, flt, ["distinguishedName", "sAMAccountName", LDAP_HIORG_ID_ATTR])


def _move_if_needed(conn: Connection, dn: str, target_ou: str) -> str:
    if not LDAP_MOVE_IF_OU_CHANGED:
        return dn
    if dn.lower().endswith("," + target_ou.lower()):
        return dn
    # DN move: keep CN part
    rdn = dn.split(",", 1)[0]  # "CN=..."
    ok = conn.modify_dn(dn, relative_dn=rdn, new_superior=target_ou)
    if not ok:
        # wenn move nicht geht -> DN unverändert lassen, aber nicht crashen
        return dn
    # neues DN: rdn + "," + target_ou
    return f"{rdn},{target_ou}"


# -----------------------------
# Group sync helpers (AD groups)
# -----------------------------
LDAP_GROUP_MEMBER_ATTR = os.getenv("LDAP_GROUP_MEMBER_ATTR", "member")
LDAP_GROUP_SYNC_REMOVE = os.getenv("LDAP_GROUP_SYNC_REMOVE", "false").lower() in ("1", "true", "yes")


def _resolve_ad_group_dn(ov: str, hiorg_group_name: str) -> tuple[str | None, str]:
    """
    Returns (group_dn or None, reason)
    Mapping in groupmap.json:
      locations: {"Wache Mitte":{"base_dn":"OU=...,DC=..."}}
      groups: {"Atemschutz":{"location":"Wache Mitte","base_dn":"","ad_cn":""}}
    """
    m = _load_groupmap(ov)
    gcfg = (m.get("groups") or {}).get(hiorg_group_name)
    if not gcfg:
        return None, "no_mapping"

    base_dn = (gcfg.get("base_dn") or "").strip()
    if not base_dn:
        loc = (gcfg.get("location") or "").strip()
        base_dn = ((m.get("locations") or {}).get(loc, {}) or {}).get("base_dn", "").strip()

    if not base_dn:
        return None, "no_base_dn"

    ad_cn = (gcfg.get("ad_cn") or "").strip()
    cn = ad_cn if ad_cn else hiorg_group_name
    cn = (cn or "").strip()

    # If 'cn' already looks like a full DN, keep it as-is
    if "," in cn and cn.upper().startswith("CN="):
        return cn, "ok"

    # If 'cn' already contains the 'CN=' prefix, strip it
    if cn.upper().startswith("CN="):
        cn = cn[3:].strip()

    cn_esc = escape_rdn(cn)
    return f"CN={cn_esc},{base_dn}", "ok"


def _group_exists(conn: Connection, group_dn: str) -> bool:
    ok = conn.search(
        search_base=group_dn,
        search_filter="(objectClass=group)",
        search_scope=BASE,
        attributes=["distinguishedName"],
        size_limit=1,
    )
    return bool(ok and conn.entries)


def _sync_user_groups(conn: Connection, ov: str, user_dn: str, person: dict) -> dict:
    desired = set(_hiorg_groups(person))
    desired = {str(x).strip() for x in desired if str(x).strip()}

    m = _load_groupmap(ov)
    managed_groups = set((m.get("groups") or {}).keys())

    add_ok, add_skipped, remove_ok = [], [], []

    # ADD (nur wenn Gruppe existiert)
    for g in sorted(desired):
        group_dn, reason = _resolve_ad_group_dn(ov, g)
        if not group_dn:
            add_skipped.append({"group": g, "reason": reason})
            continue
        if not _group_exists(conn, group_dn):
            add_skipped.append({"group": g, "reason": "ad_group_missing", "dn": group_dn})
            continue

        ok = conn.modify(group_dn, {LDAP_GROUP_MEMBER_ATTR: [(MODIFY_ADD, [user_dn])]})
        res = conn.result or {}
        code = res.get("result")
        if ok or code in (0, 20):  # 20 = attributeOrValueExists
            add_ok.append({"group": g, "dn": group_dn})
        else:
            add_skipped.append({"group": g, "reason": f"ldap_error_{code}", "dn": group_dn, "detail": res})

    # REMOVE (optional): nur user_dn entfernen, niemals REPLACE!
    if LDAP_GROUP_SYNC_REMOVE:
        to_remove = sorted((managed_groups - desired))
        for g in to_remove:
            group_dn, reason = _resolve_ad_group_dn(ov, g)
            if not group_dn or not _group_exists(conn, group_dn):
                continue
            ok = conn.modify(group_dn, {LDAP_GROUP_MEMBER_ATTR: [(MODIFY_DELETE, [user_dn])]})
            if ok:
                remove_ok.append({"group": g, "dn": group_dn})

    return {"desired_count": len(desired), "added": add_ok, "skipped": add_skipped, "removed": remove_ok}




# -----------------------------
# Routes
# -----------------------------
@app.get("/health")
def health():
    return {"ok": True, "app": APP_NAME}


@app.get("/oauth/start")
def oauth_start(ov: str):
    _require_ov(ov)
    if not (HIORG_CLIENT_ID and HIORG_CLIENT_SECRET and HIORG_REDIRECT_URI):
        raise HTTPException(500, "Missing HIORG_CLIENT_ID/SECRET/REDIRECT_URI env vars")

    state = _gen_state(ov)
    states = _load_states()
    states[state] = {"ov": ov, "ts": int(time.time())}
    _save_states(states)

    q = {
        "response_type": "code",
        "client_id": HIORG_CLIENT_ID,
        "redirect_uri": HIORG_REDIRECT_URI,
        "scope": HIORG_SCOPE,  # <- wichtig: scope bleibt hier drin
        "state": state,
    }
    return RedirectResponse(f"{HIORG_AUTH_URL}?{urlencode(q)}")


@app.get("/oauth/callback")
def oauth_callback(code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if error:
        raise HTTPException(400, f"OAuth error: {error} {error_description}".strip())
    if not code or not state:
        raise HTTPException(400, "Missing code/state")

    states = _load_states()
    entry = states.get(state)
    if not entry:
        raise HTTPException(400, "Invalid/expired state")
    ov = entry.get("ov", "")
    _require_ov(ov)

    states.pop(state, None)
    _save_states(states)

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": HIORG_REDIRECT_URI,
        "client_id": HIORG_CLIENT_ID,
        "client_secret": HIORG_CLIENT_SECRET,
    }
    tokens = _token_request(payload)
    _save_tokens(ov, tokens)

    if not _marker_path(ov).exists():
        _set_marker(ov, _get_marker(ov))

    return JSONResponse({"ok": True, "ov": ov, "stored": True, "next": f"/sync/run?ov={ov}"})



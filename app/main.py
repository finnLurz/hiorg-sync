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


@app.get("/sync/run")
def sync_run(request: Request, ov: str = ""):
    _require_api_key(request)

    ovs = [ov] if ov else (OV_LIST or [])
    if not ovs:
        raise HTTPException(400, "No ov given and OV_LIST is empty")
    for one in ovs:
        _require_ov(one)

    results = []
    for one in ovs:
        tokens = _refresh_tokens(one)
        access = tokens.get("access_token")
        if not access:
            raise HTTPException(500, f"No access_token after refresh for ov '{one}'")

        marker = _get_marker(one)
        people = _fetch_personal_updated_since(access, marker)

        new_marker = _iso(_now_utc() - timedelta(seconds=120))
        _set_marker(one, new_marker)

        results.append({"ov": one, "updated_since_used": marker, "fetched": len(people), "new_marker": new_marker})

    return {"ok": True, "results": results}


@app.get("/debug/personal")
def debug_personal(request: Request, ov: str, limit: int = 3):
    _require_api_key(request)
    _require_ov(ov)

    tokens = _refresh_tokens(ov)
    access = tokens.get("access_token")
    if not access:
        raise HTTPException(500, f"No access_token after refresh for ov '{ov}'")

    marker = _get_marker(ov)
    people = _fetch_personal_updated_since(access, marker)

    sample = people[: max(0, min(limit, 20))]
    top_keys = sorted({k for p in sample for k in (p.keys() if isinstance(p, dict) else [])})

    return {
        "ov": ov,
        "updated_since_used": marker,
        "count_total_fetched": len(people),
        "sample_count": len(sample),
        "sample_top_keys": top_keys,
        "sample": sample,
    }


@app.get("/debug/admap")
def debug_admap(request: Request, ov: str, limit: int = 3, dry_run: int = 1):
    """
    Zeigt dir: welche AD-Attribute würden wir setzen (inkl. sam, employeeID=hiorg-...)
    """
    _require_api_key(request)
    _require_ov(ov)

    tokens = _refresh_tokens(ov)
    access = tokens.get("access_token")
    marker = _get_marker(ov)
    people = _fetch_personal_updated_since(access, marker)

    ou_map = _load_ou_map()
    target_ou = ou_map.get(ov.lower())
    if not target_ou:
        raise HTTPException(500, f"No OU mapping for ov '{ov}'. Set LDAP_OU_MAP in .env")

    # Limit sample
    people = people[: max(0, min(limit, 20))]

    # Build preview
    conn = _ldap_conn()
    out = []
    for p in people:
        attrs = p.get("attributes") or {}
        if (attrs.get("orgakuerzel") or "").lower() in EXCLUDE_ORGAKUERZEL:
            continue
        if LDAP_ONLY_STATUS_ACTIVE and (attrs.get("status") != "aktiv"):
            continue

        base_sam, h_username = _sam_base_from_person(attrs)
        sam = _ensure_unique_sam(conn, target_ou, base_sam, h_username)

        mapped = _map_person_to_ad_attrs(p, sam)
        dn = f"CN={escape_rdn(mapped.get('displayName','User'))},{target_ou}"
        out.append({"person_id": p.get("id"), "dn": dn, "sam": sam, "attrs": mapped})

    conn.unbind()
    return {"ov": ov, "target_ou": target_ou, "count_preview": len(out), "preview": out, "dry_run": bool(dry_run)}

@app.get("/api/groups")
def api_groups(request: Request, ov: str, days: int = 3650):
    _require_api_or_ui(request)
    _require_ov(ov)

    tokens = _refresh_tokens(ov)
    access = tokens.get("access_token")
    if not access:
        raise HTTPException(500, f"No access_token after refresh for ov '{ov}'")

    # Discovery unabhängig vom Sync-Marker (damit UI alles sieht)
    marker = _iso(_now_utc() - timedelta(days=days))
    people = _fetch_personal_updated_since(access, marker)

    found: dict[str, dict] = {}

    for p in people:
        attrs = p.get("attributes") or {}
        if (attrs.get("orgakuerzel") or "").lower() in EXCLUDE_ORGAKUERZEL:
            continue
        if LDAP_ONLY_STATUS_ACTIVE and (attrs.get("status") != "aktiv"):
            continue

        loc = _person_location(attrs)
        for g in _hiorg_groups(p):
            g = str(g).strip()
            if not g:
                continue
            loc2, g2 = _split_group_location(g)
            loc_final = loc or loc2 or "Unbekannt"
            found.setdefault(g2, {"locations": set()})["locations"].add(loc_final)

    out = [{"group": k, "locations": sorted(list(v["locations"]))} for k, v in sorted(found.items())]
    return {"ov": ov, "count": len(out), "groups": out}


@app.get("/api/groupmap")
def api_groupmap_get(request: Request, ov: str):
    _require_api_or_ui(request)
    _require_ov(ov)
    return {"ov": ov, "map": _load_groupmap(ov)}

@app.post("/api/groupmap")
async def api_groupmap_post(request: Request, ov: str):
    _require_api_or_ui(request)
    _require_ov(ov)
    body = await request.json()
    m = body.get("map")
    if not isinstance(m, dict):
        raise HTTPException(400, "body.map must be a dict")
    _save_groupmap(ov, m)
    return {"ok": True}

@app.get("/ui/login", response_class=HTMLResponse)
def ui_login_get(request: Request, next: str = "/ui/groupmap"):
    html = f"""
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>Login</title>
  <style>
    body {{ font-family: sans-serif; margin: 40px; max-width: 520px; }}
    input {{ width: 100%; padding: 10px; font-size: 16px; }}
    button {{ padding: 10px 16px; font-size: 16px; margin-top: 10px; }}
    .hint {{ color: #666; font-size: 13px; margin-top: 10px; }}
  </style>
</head>
<body>
  <h2>HiOrg-Sync Login</h2>
  <form method="post" action="/ui/login">
    <input type="hidden" name="next" value="{next}">
    <label>Passwort</label><br>
    <input type="password" name="password" autofocus>
    <button type="submit">Anmelden</button>
  </form>
  <div class="hint">Session läuft nach {UI_SESSION_TTL_HOURS}h ab.</div>
</body>
</html>
"""
    return html


@app.post("/ui/login")
async def ui_login_post(request: Request):
    form = await request.form()
    pw = str(form.get("password", "") or "")
    nxt = str(form.get("next", "") or "/ui/groupmap")
    if UI_PASSWORD and not secrets.compare_digest(pw, UI_PASSWORD):
        raise HTTPException(401, "Invalid password")

    token = _ui_make_session()
    resp = RedirectResponse(url=nxt, status_code=302)
    resp.set_cookie(
        "ui_session",
        token,
        httponly=True,
        secure=False,   # wenn du hinter HTTPS-Proxy bist -> True setzen
        samesite="lax",
        max_age=UI_SESSION_TTL_HOURS * 3600,
    )
    return resp



@app.get("/ui/groupmap", response_class=HTMLResponse)
def ui_groupmap(request: Request, ov: str):
    try:
        _require_ui_login(request)
    except HTTPException:
        return RedirectResponse(f"/ui/login?next=/ui/groupmap?ov={ov}", status_code=302)

    _require_ov(ov)
    # UI selbst nicht über Header schützen, weil Browser. Du kannst stattdessen SYNC_API_KEY weglassen
    # oder du setzt SYNC_API_KEY und trägst ihn unten im JS ein (oder machst HTTPBasic).
    # Minimal: wenn SYNC_API_KEY gesetzt ist, verlangt die Seite die Eingabe und sendet sie als Header.
    html = f"""
<!doctype html>
<html lang="de">
<head>
  <meta charset="utf-8">
  <title>HiOrg → AD Gruppen-Mapping ({ov})</title>
  <style>
    body {{ font-family: sans-serif; margin: 20px; }}
    .row {{ display: grid; grid-template-columns: 2fr 1fr 2fr 2fr; gap: 10px; padding: 6px 0; border-bottom: 1px solid #eee; }}
    input {{ width: 100%; }}
    .hdr {{ font-weight: bold; border-bottom: 2px solid #ccc; padding-bottom: 8px; }}
    .small {{ color: #666; font-size: 12px; }}
  </style>
</head>
<body>
  <h2>Gruppen-Mapping ({ov})</h2>
  <div class="small">
    Ziel: HiOrg-Gruppen (nach Standort) einer <b>GroupBaseDN</b> zuordnen. Gruppen werden <b>nicht</b> automatisch angelegt.
  </div>

  <p>
    <button onclick="loadAll()">Laden</button>
    <button onclick="saveAll()">Speichern</button>
  </p>

  <h3>Standort → BaseDN</h3>
  <div id="locs"></div>
  <button onclick="addLoc()">+ Standort</button>

  <h3 style="margin-top: 28px;">Gruppen</h3>
  <div class="row hdr">
    <div>HiOrg Gruppe</div><div>Standort</div><div>BaseDN</div><div>AD Gruppen-CN (optional)</div>
  </div>
  <div id="groups"></div>

<script>
let groupMap = null;
let discovered = null;

function apiHeaders() {{
  return {{'Content-Type': 'application/json'}};
}}

async function loadAll() {{
  const gm = await fetch('/api/groupmap?ov={ov}', {{headers: apiHeaders()}});
  groupMap = (await gm.json()).map;

  const dg = await fetch('/api/groups?ov={ov}', {{headers: apiHeaders()}});
  discovered = (await dg.json()).groups;

  render();
}}

function render() {{
  // locations
  const locs = document.getElementById('locs');
  locs.innerHTML = '';
  const locEntries = Object.entries(groupMap.locations || {{}});
  locEntries.sort((a,b)=>a[0].localeCompare(b[0]));
  for (const [loc, cfg] of locEntries) {{
    const base = (cfg && cfg.base_dn) ? cfg.base_dn : '';
    const div = document.createElement('div');
    div.className = 'row';
    div.innerHTML = `
      <input value="${{loc}}" onchange="renameLoc('${{loc}}', this.value)">
      <div></div>
      <input value="${{base}}" placeholder="OU=Groups,DC=example,DC=local" onchange="setLocBase('${{loc}}', this.value)">
      <button onclick="delLoc('${{loc}}')">Löschen</button>
    `;
    locs.appendChild(div);
  }}

  // groups
  const gdiv = document.getElementById('groups');
  gdiv.innerHTML = '';

  // initial: discovered groups -> ensure entry in groupMap.groups
  groupMap.groups = groupMap.groups || {{}};
  for (const g of (discovered || [])) {{
    const name = g.group;
    if (!groupMap.groups[name]) {{
      const loc = (g.locations && g.locations[0]) ? g.locations[0] : 'Unbekannt';
      const base_dn = (groupMap.locations?.[loc]?.base_dn) || '';
      groupMap.groups[name] = {{location: loc, base_dn: base_dn, ad_cn: ''}};
    }}
  }}

  const entries = Object.entries(groupMap.groups);
  entries.sort((a,b)=>a[0].localeCompare(b[0]));
  for (const [gname, cfg] of entries) {{
    const loc = cfg.location || 'Unbekannt';
    const base_dn = cfg.base_dn || (groupMap.locations?.[loc]?.base_dn) || '';
    const ad_cn = cfg.ad_cn || '';
    const row = document.createElement('div');
    row.className = 'row';
    row.innerHTML = `
      <div>${{gname}}</div>
      <input value="${{loc}}" onchange="setGroup('${{gname}}', 'location', this.value)">
      <input value="${{base_dn}}" placeholder="GroupBaseDN" onchange="setGroup('${{gname}}', 'base_dn', this.value)">
      <input value="${{ad_cn}}" placeholder="leer = CN=HiOrgName" onchange="setGroup('${{gname}}', 'ad_cn', this.value)">
    `;
    gdiv.appendChild(row);
  }}
}}

function addLoc() {{
  const name = prompt('Standort-Name?');
  if (!name) return;
  groupMap.locations = groupMap.locations || {{}};
  groupMap.locations[name] = {{base_dn: ''}};
  render();
}}

function renameLoc(oldName, newName) {{
  if (!newName || newName === oldName) return;
  const cfg = groupMap.locations[oldName];
  delete groupMap.locations[oldName];
  groupMap.locations[newName] = cfg;
  // update group references
  for (const g in groupMap.groups) {{
    if (groupMap.groups[g].location === oldName) groupMap.groups[g].location = newName;
  }}
  render();
}}

function setLocBase(loc, base) {{
  groupMap.locations[loc] = groupMap.locations[loc] || {{}};
  groupMap.locations[loc].base_dn = base;
}}

function delLoc(loc) {{
  if (!confirm('Standort wirklich löschen?')) return;
  delete groupMap.locations[loc];
  render();
}}

function setGroup(g, k, v) {{
  groupMap.groups[g] = groupMap.groups[g] || {{}};
  groupMap.groups[g][k] = v;
}}

async function saveAll() {{
  const r = await fetch('/api/groupmap?ov={ov}', {{
    method: 'POST',
    headers: apiHeaders(),
    body: JSON.stringify({{map: groupMap}})
  }});
  const j = await r.json();
  alert(j.ok ? 'Gespeichert' : 'Fehler');
}}
</script>
</body>
</html>
"""
    return html



@app.get("/sync/ad")
def sync_ad(request: Request, ov: str, limit: int = 0, dry_run: int = 0):
    """
    Holt HiOrg /personal (updated_since marker) und schreibt nach AD:
    - OU nach LDAP_OU_MAP[ov]
    - employeeID = hiorg-<id> (oder konfiguriertes LDAP_HIORG_ID_ATTR)
    - Telefon / Mail / Adresse
    - sAMAccountName = vorname.nachname (unique), fallback username
    """
    _require_api_key(request)
    _require_ov(ov)

    ou_map = _load_ou_map()
    target_ou = ou_map.get(ov.lower())
    if not target_ou:
        raise HTTPException(500, f"No OU mapping for ov '{ov}'. Set LDAP_OU_MAP in .env")

    tokens = _refresh_tokens(ov)
    access = tokens.get("access_token")
    if not access:
        raise HTTPException(500, f"No access_token after refresh for ov '{ov}'")

    marker = _get_marker(ov)
    people = _fetch_personal_updated_since(access, marker)

    if limit and limit > 0:
        people = people[:limit]

    conn = _ldap_conn()

    results = []
    incoming = 0

    for p in people:
        attrs = p.get("attributes") or {}
        org = (attrs.get("orgakuerzel") or "").lower()
        status = attrs.get("status") or ""

        if org in EXCLUDE_ORGAKUERZEL:
            continue
        if LDAP_ONLY_STATUS_ACTIVE and status != "aktiv":
            continue

        incoming += 1

        # stable key
        hid = _build_hiorg_id(p)

        # find existing by employeeID (or configured attribute)
        existing = _find_existing_by_hiorg_id(conn, target_ou, hid)

        # sam generation (unique in OU)
        base_sam, fallback_username = _sam_base_from_person(attrs)
        sam = None

        if existing and not LDAP_UPDATE_SAM:
            # keep existing samAccountName
            sam = (existing.get("attributes") or {}).get("sAMAccountName")
            if isinstance(sam, list) and sam:
                sam = sam[0]

            # falls leer/kaputt: neu generieren
            if not isinstance(sam, str) or not sam:
                sam = _ensure_unique_sam(conn, target_ou, base_sam, fallback_username)
        else:
            # create OR rename allowed
            sam = _ensure_unique_sam(conn, target_ou, base_sam, fallback_username)



        mapped = _map_person_to_ad_attrs(p, sam)
        display = mapped.get("displayName") or mapped.get("cn") or "User"
        dn_target = f"CN={escape_rdn(display)},{target_ou}"

        if dry_run:
            desired = sorted({str(x).strip() for x in _hiorg_groups(p) if str(x).strip()})
            resolved = []
            for g in desired:
                dn, reason = _resolve_ad_group_dn(ov, g)
                resolved.append({"group": g, "ad_dn": dn, "reason": reason})

            results.append({
                "person_id": p.get("id"),
                "action": "dry_run",
                "dn": dn_target,
                "sam": sam,
                "mapped_keys": sorted(mapped.keys()),
                "group_plan": resolved,
            })
            continue

        if not existing:
            ok = conn.add(dn_target, attributes=mapped)
            group_sync = {}
            if ok:
                group_sync = _sync_user_groups(conn, ov, dn_target, p)

            results.append({
                "person_id": p.get("id"),
                "action": "create",
                "ok": bool(ok),
                "dn": dn_target,
                "sam": sam,
                "group_sync": group_sync,
                "result": conn.result,
            })
            continue

        # update existing entry
        dn_existing = existing["dn"]
        dn_after = _move_if_needed(conn, dn_existing, target_ou)

        changes: dict[str, Any] = {}
        # kontakt / adresse / displayname etc. (modify)
        _ldap_attr_set(changes, "givenName", mapped.get("givenName"))
        _ldap_attr_set(changes, "sn", mapped.get("sn"))
        _ldap_attr_set(changes, "displayName", mapped.get("displayName"))
        _ldap_attr_set(changes, "mail", mapped.get("mail"))
        _ldap_attr_set(changes, "telephoneNumber", mapped.get("telephoneNumber"))
        _ldap_attr_set(changes, "homePhone", mapped.get("homePhone"))
        _ldap_attr_set(changes, "mobile", mapped.get("mobile"))
        _ldap_attr_set(changes, "streetAddress", mapped.get("streetAddress"))
        _ldap_attr_set(changes, "postalCode", mapped.get("postalCode"))
        _ldap_attr_set(changes, "l", mapped.get("l"))
        _ldap_attr_set(changes, "co", mapped.get("co"))
        _ldap_attr_set(changes, "description", mapped.get("description"))
        # hiorg-id key
        if hid:
            _ldap_attr_set(changes, LDAP_HIORG_ID_ATTR, hid)

        ok = True
        if changes:
            ok = conn.modify(dn_after, changes)

        group_sync = {}
        if ok:
            group_sync = _sync_user_groups(conn, ov, dn_after, p)

        results.append(
            {
                "person_id": p.get("id"),
                "action": "update",
                "ok": bool(ok),
                "dn": dn_after,
                "sam": sam,
                "changed_attrs": sorted(changes.keys()),
                "group_sync": group_sync,
                "result": conn.result,
            }
        )


        # Gruppen-Sync später: hier hättest du _hiorg_groups(p)

    conn.unbind()

    # marker setzen
    new_marker = _iso(_now_utc() - timedelta(seconds=120))
    _set_marker(ov, new_marker)

    return {
        "ov": ov,
        "target_ou": target_ou,
        "updated_since_used": marker,
        "new_marker": new_marker,
        "count_incoming": incoming,
        "results": results,
        "notes": {
            "overwrite_empty": LDAP_OVERWRITE_EMPTY,
            "only_status_active": LDAP_ONLY_STATUS_ACTIVE,
            "excluded_orgakuerzel": sorted(list(EXCLUDE_ORGAKUERZEL)),
            "hiorg_id_attr": LDAP_HIORG_ID_ATTR,
        },
    }

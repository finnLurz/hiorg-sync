# src/hiorg_sync/services/ldap.py
from __future__ import annotations

import json
import re
import secrets
import unicodedata
from typing import Any

from fastapi import HTTPException
from ldap3 import (
    BASE,
    SUBTREE,
    Connection,
    Server,
    MODIFY_ADD,
    MODIFY_DELETE,
    MODIFY_REPLACE,
)
from ldap3.utils.dn import escape_rdn

from ..core.settings import (
    LDAP_URL,
    LDAP_BIND_USER,
    LDAP_BIND_PASSWORD,
    LDAP_OU_MAP_JSON,
    LDAP_DEFAULT_DOMAIN,
    LDAP_OVERWRITE_EMPTY,
    LDAP_CREATE_ENABLED,
    LDAP_MOVE_IF_OU_CHANGED,
    LDAP_SAM_MODE,
    LDAP_SAM_USERNAME_KEY,
    LDAP_UPDATE_SAM,
    LDAP_HIORG_ID_ATTR,
    LDAP_HIORG_ID_PREFIX,
)


# -----------------------------
# Connection / config
# -----------------------------
def ldap_parse_url(url: str) -> tuple[str, int, bool]:
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


def ldap_conn() -> Connection:
    if not (LDAP_URL and LDAP_BIND_USER and LDAP_BIND_PASSWORD):
        raise HTTPException(500, "LDAP_URL / LDAP_BIND_USER / LDAP_BIND_PASSWORD missing")

    host, port, use_ssl = ldap_parse_url(LDAP_URL)
    server = Server(host, port=port, use_ssl=use_ssl, get_info=None)
    return Connection(server, user=LDAP_BIND_USER, password=LDAP_BIND_PASSWORD, auto_bind=True)


def load_ou_map() -> dict[str, str]:
    try:
        m = json.loads(LDAP_OU_MAP_JSON or "{}")
        if not isinstance(m, dict):
            return {}
        return {str(k).lower(): str(v) for k, v in m.items()}
    except Exception:
        return {}


# -----------------------------
# Search helpers
# -----------------------------
def ldap_search_one(conn: Connection, base_dn: str, flt: str, attrs: list[str]) -> dict | None:
    ok = conn.search(
        search_base=base_dn,
        search_filter=flt,
        search_scope=SUBTREE,
        attributes=attrs,
        size_limit=1,
    )
    if not ok or not conn.entries:
        return None
    e = conn.entries[0]
    return {"dn": e.entry_dn, "attributes": e.entry_attributes_as_dict}


def entry_exists(conn: Connection, dn: str, objectclass: str) -> bool:
    ok = conn.search(
        search_base=dn,
        search_filter=f"(objectClass={objectclass})",
        search_scope=BASE,
        attributes=["distinguishedName"],
        size_limit=1,
    )
    return bool(ok and conn.entries)


# -----------------------------
# Attribute update helpers
# -----------------------------
def ldap_attr_set(changes: dict, attr: str, value: str | None) -> None:
    """
    - None => ignore (no change)
    - ""   => set empty only if LDAP_OVERWRITE_EMPTY=True, else ignore
    - else => replace with value
    """
    if value is None:
        return
    value = str(value).strip()

    if value == "" and not LDAP_OVERWRITE_EMPTY:
        return

    if value == "":
        changes[attr] = [(MODIFY_REPLACE, [])]
    else:
        changes[attr] = [(MODIFY_REPLACE, [value])]


# -----------------------------
# Username / sAMAccountName helpers
# -----------------------------
def normalize_ascii(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    s = (
        s.replace("ä", "ae")
        .replace("ö", "oe")
        .replace("ü", "ue")
        .replace("Ä", "Ae")
        .replace("Ö", "Oe")
        .replace("Ü", "Ue")
        .replace("ß", "ss")
    )
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


def clean_sam_piece(s: str) -> str:
    s = normalize_ascii(s).lower()
    s = re.sub(r"[^a-z0-9.]+", ".", s)
    s = re.sub(r"\.+", ".", s).strip(".")
    return s


def sam_base(first: str, last: str) -> str:
    f = clean_sam_piece(first)
    l = clean_sam_piece(last)
    if f and l:
        return f"{f}.{l}"
    return f or l or "user"


def sam_short(base: str) -> str:
    # AD sAMAccountName: effektiv max 20
    if len(base) <= 20:
        return base
    parts = base.split(".")
    if len(parts) >= 2:
        f0 = parts[0][:1]
        l = ".".join(parts[1:])
        cand = f"{f0}.{l}"
        if len(cand) <= 20:
            return cand
        l2 = l[: max(1, 20 - 2)]  # 1 + '.' + rest
        return f"{f0}.{l2}"[:20]
    return base[:20]


def sam_base_from_person(attrs: dict) -> tuple[str, str]:
    """
    Returns (base_sam, fallback_username_raw)
    base_sam wird nach LDAP_SAM_MODE gebaut (noch NICHT unique geprüft).
    """
    h_username = str(attrs.get(LDAP_SAM_USERNAME_KEY, "") or "").strip()

    if LDAP_SAM_MODE in ("hiorg", "hiorg_username", "username"):
        base = clean_sam_piece(h_username)
        if not base:
            base = sam_base(str(attrs.get("vorname", "") or ""), str(attrs.get("nachname", "") or ""))
    else:
        base = sam_base(str(attrs.get("vorname", "") or ""), str(attrs.get("nachname", "") or ""))

    return base, h_username


def ensure_unique_sam(conn: Connection, search_base: str, base_sam: str, fallback_username: str) -> str:
    base_sam = sam_short(base_sam)
    if not ldap_search_one(conn, search_base, f"(sAMAccountName={base_sam})", ["sAMAccountName"]):
        return base_sam

    for i in range(2, 1000):
        suffix = str(i)
        cut = 20 - len(suffix)
        cand = (base_sam[:cut] + suffix)[:20]
        if not ldap_search_one(conn, search_base, f"(sAMAccountName={cand})", ["sAMAccountName"]):
            return cand

    fb = sam_short(clean_sam_piece(fallback_username) or "user")
    if not ldap_search_one(conn, search_base, f"(sAMAccountName={fb})", ["sAMAccountName"]):
        return fb

    return ("u" + secrets.token_hex(8))[:20]


# -----------------------------
# DN / OU moves
# -----------------------------
def move_if_needed(conn: Connection, dn: str, target_ou: str) -> str:
    if not LDAP_MOVE_IF_OU_CHANGED:
        return dn
    if dn.lower().endswith("," + target_ou.lower()):
        return dn

    rdn = dn.split(",", 1)[0]  # "CN=..."
    ok = conn.modify_dn(dn, relative_dn=rdn, new_superior=target_ou)
    if not ok:
        return dn
    return f"{rdn},{target_ou}"


# -----------------------------
# HiOrg ID helpers (for AD attr mapping)
# -----------------------------
def build_hiorg_id(hiorg_person_id: str) -> str:
    pid = str(hiorg_person_id or "").strip()
    return f"{LDAP_HIORG_ID_PREFIX}{pid}" if pid else ""


# -----------------------------
# Create / update user
# -----------------------------
def build_ad_attrs_from_person(person: dict, sam: str, ov: str) -> dict[str, Any]:
    """
    person: HiOrg /personal item {id, attributes:{...}}
    """
    a = person.get("attributes") or {}

    first = str(a.get("vorname", "") or "")
    last = str(a.get("nachname", "") or "")
    display = str(a.get("name", "") or "").strip() or f"{first} {last}".strip()
    email = str(a.get("email", "") or "").strip()
    teldienst = str(a.get("teldienst", "") or "").strip()
    telpriv = str(a.get("telpriv", "") or "").strip()
    mobile = str(a.get("handy", "") or "").strip()
    street = str(a.get("adresse", "") or "").strip()
    plz = str(a.get("plz", "") or "").strip()
    city = str(a.get("ort", "") or "").strip()
    land = str(a.get("land", "") or "").strip()

    upn = f"{sam}@{LDAP_DEFAULT_DOMAIN}"

    attrs: dict[str, Any] = {
        "objectClass": ["top", "person", "organizationalPerson", "user"],
        "cn": display,
        "givenName": first,
        "sn": (last or display),
        "displayName": display,
        "sAMAccountName": sam,
        "userPrincipalName": upn,
        "description": f"HiOrg {ov}",
        # ohne Passwort besser disabled lassen, sonst brauchst du Passwort/Flags sauber
        "userAccountControl": 512 if LDAP_CREATE_ENABLED else 514,
    }

    hid = build_hiorg_id(str(person.get("id", "") or ""))
    if hid:
        attrs[LDAP_HIORG_ID_ATTR] = hid

    if email:
        attrs["mail"] = email
    if teldienst:
        attrs["telephoneNumber"] = teldienst
    if telpriv:
        attrs["homePhone"] = telpriv
    if mobile:
        attrs["mobile"] = mobile

    if street:
        attrs["streetAddress"] = street
    if plz:
        attrs["postalCode"] = plz
    if city:
        attrs["l"] = city
    if land:
        attrs["co"] = land

    return attrs


def find_existing_by_hiorg_id(conn: Connection, search_base: str, hiorg_id_value: str) -> dict | None:
    if not hiorg_id_value:
        return None
    return ldap_search_one(
        conn,
        search_base,
        f"({LDAP_HIORG_ID_ATTR}={hiorg_id_value})",
        ["distinguishedName", "sAMAccountName", LDAP_HIORG_ID_ATTR],
    )


def apply_user_changes(conn: Connection, user_dn: str, desired: dict[str, Any]) -> dict:
    """
    Applies MODIFY_REPLACE changes for common attrs.
    (objectClass/cn/etc for add must be handled in create step)
    """
    changes: dict[str, Any] = {}

    # standard string attrs we want to replace
    for k in [
        "givenName",
        "sn",
        "displayName",
        "userPrincipalName",
        "description",
        LDAP_HIORG_ID_ATTR,
        "mail",
        "telephoneNumber",
        "homePhone",
        "mobile",
        "streetAddress",
        "postalCode",
        "l",
        "co",
    ]:
        if k in desired:
            ldap_attr_set(changes, k, desired.get(k))

    # optional rename sAMAccountName only if enabled
    if LDAP_UPDATE_SAM and "sAMAccountName" in desired:
        ldap_attr_set(changes, "sAMAccountName", desired.get("sAMAccountName"))

    if not changes:
        return {"ok": True, "changed": False}

    ok = conn.modify(user_dn, changes)
    return {"ok": bool(ok), "changed": True, "result": conn.result}


def create_user(conn: Connection, target_ou: str, attrs: dict[str, Any]) -> dict:
    cn = str(attrs.get("cn", "User") or "User").strip() or "User"
    dn = f"CN={escape_rdn(cn)},{target_ou}"

    ok = conn.add(dn, attributes=attrs)
    if not ok:
        raise HTTPException(500, f"LDAP add failed: {conn.result}")
    return {"dn": dn, "result": conn.result}


# -----------------------------
# Group membership helpers (optional)
# -----------------------------
def group_dn_from_cn(base_dn: str, cn: str) -> str:
    cn = cn.strip()
    if "," in cn and cn.upper().startswith("CN="):
        return cn
    if cn.upper().startswith("CN="):
        cn = cn[3:].strip()
    return f"CN={escape_rdn(cn)},{base_dn}"


def add_user_to_group(conn: Connection, group_dn: str, user_dn: str, member_attr: str = "member") -> dict:
    ok = conn.modify(group_dn, {member_attr: [(MODIFY_ADD, [user_dn])]})
    res = conn.result or {}
    # 0=success, 20=attributeOrValueExists
    if ok or res.get("result") in (0, 20):
        return {"ok": True, "result": res}
    return {"ok": False, "result": res}


def remove_user_from_group(conn: Connection, group_dn: str, user_dn: str, member_attr: str = "member") -> dict:
    ok = conn.modify(group_dn, {member_attr: [(MODIFY_DELETE, [user_dn])]})
    return {"ok": bool(ok), "result": conn.result}

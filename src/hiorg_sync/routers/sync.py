# src/hiorg_sync/routers/sync.py
from __future__ import annotations

import json
import os
import re
import secrets
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, HTTPException
from ldap3 import (
    MODIFY_ADD,
    MODIFY_DELETE,
    MODIFY_REPLACE,
    BASE,
    SUBTREE,
)
from ldap3.utils.dn import escape_rdn

from ..core.security import require_api_key
from ..core.settings import (
    # OV handling (aus .env)
    require_ov,
    get_ov_list,

    # storage/marker
    DATA_DIR,
    INITIAL_SYNC_DAYS,

    # HiOrg -> Filter
    EXCLUDE_ORGAKUERZEL,
    LDAP_ONLY_STATUS_ACTIVE,

    # LDAP mapping behavior
    LDAP_OVERWRITE_EMPTY,
    LDAP_DEFAULT_DOMAIN,
    LDAP_CREATE_ENABLED,
    LDAP_MOVE_IF_OU_CHANGED,

    # sam generation
    LDAP_SAM_MODE,
    LDAP_SAM_USERNAME_KEY,
    LDAP_UPDATE_SAM,

    # HiOrg ID mapping
    LDAP_HIORG_ID_ATTR,
    LDAP_HIORG_ID_PREFIX,

    # group sync config
    HIORG_LOCATION_KEY,
    HIORG_GROUP_SPLIT_RE,
    LDAP_GROUP_MEMBER_ATTR,
    LDAP_GROUP_SYNC_REMOVE,
)
from ..services.hiorg import refresh_tokens, fetch_personal_updated_since
from ..services.ldap import ldap_conn, load_ou_map, ldap_search_one
from ..services.groupmap_store import load_groupmap

router = APIRouter()


# -----------------------------
# marker storage (updated_since)
# -----------------------------
def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _ov_dir(ov: str) -> Path:
    d = Path(DATA_DIR) / ov
    d.mkdir(parents=True, exist_ok=True)
    return d


def _marker_path(ov: str) -> Path:
    return _ov_dir(ov) / "updated_since.txt"


def _get_marker(ov: str) -> str:
    p = _marker_path(ov)
    if p.exists():
        v = p.read_text(encoding="utf-8").strip()
        if v:
            return v
    return _iso(_now_utc() - timedelta(days=int(INITIAL_SYNC_DAYS)))


def _set_marker(ov: str, marker: str) -> None:
    _marker_path(ov).write_text(marker.strip() + "\n", encoding="utf-8")


# -----------------------------
# HiOrg helpers
# -----------------------------
def _hiorg_attr(person: dict, key: str, default: str = "") -> str:
    a = person.get("attributes") or {}
    v = a.get(key, default)
    if v is None:
        return ""
    return v if isinstance(v, str) else str(v)


def _hiorg_groups(person: dict) -> list[str]:
    a = person.get("attributes") or {}
    g = a.get("gruppen_namen")
    if isinstance(g, list):
        return [str(x) for x in g]
    return []


def _person_location(attrs: dict) -> str:
    return str(attrs.get(HIORG_LOCATION_KEY, "") or "").strip()


def _split_group_location(group_name: str) -> tuple[str, str]:
    parts = re.split(HIORG_GROUP_SPLIT_RE, group_name, maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "", group_name.strip()


def _build_hiorg_id(person: dict) -> str:
    pid = str(person.get("id", "") or "").strip()
    return f"{LDAP_HIORG_ID_PREFIX}{pid}" if pid else ""


# -----------------------------
# sAMAccountName generation
# -----------------------------
def _normalize_ascii(s: str) -> str:
    if not s:
        return ""
    s = s.strip()
    s = (
        s.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
        .replace("Ä", "Ae").replace("Ö", "Oe").replace("Ü", "Ue")
        .replace("ß", "ss")
    )
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")


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
    if len(base) <= 20:
        return base
    parts = base.split(".")
    if len(parts) >= 2:
        f0 = parts[0][:1]
        l = ".".join(parts[1:])
        cand = f"{f0}.{l}"
        if len(cand) <= 20:
            return cand
        l2 = l[: max(1, 20 - 2)]
        return f"{f0}.{l2}"[:20]
    return base[:20]


def _sam_base_from_person(attrs: dict) -> tuple[str, str]:
    """
    Returns (base_sam, fallback_username_raw)
    """
    h_username = str(attrs.get(LDAP_SAM_USERNAME_KEY, "") or "").strip()
    if str(LDAP_SAM_MODE).lower() in ("hiorg", "hiorg_username", "username"):
        base = _clean_sam_piece(h_username)
        if not base:
            base = _sam_base(str(attrs.get("vorname", "") or ""), str(attrs.get("nachname", "") or ""))
    else:
        base = _sam_base(str(attrs.get("vorname", "") or ""), str(attrs.get("nachname", "") or ""))
    return base, h_username


def _ensure_unique_sam(conn, search_base: str, base_sam: str, fallback_username: str) -> str:
    base_sam = _sam_short(base_sam)
    if not ldap_search_one(conn, search_base, f"(sAMAccountName={base_sam})", ["sAMAccountName"]):
        return base_sam

    for i in range(2, 1000):
        suffix = str(i)
        cut = 20 - len(suffix)
        cand = (base_sam[:cut] + suffix)[:20]
        if not ldap_search_one(conn, search_base, f"(sAMAccountName={cand})", ["sAMAccountName"]):
            return cand

    fb = _sam_short(_clean_sam_piece(fallback_username) or "user")
    if not ldap_search_one(conn, search_base, f"(sAMAccountName={fb})", ["sAMAccountName"]):
        return fb

    return ("u" + secrets.token_hex(8))[:20]


# -----------------------------
# AD mapping + updates
# -----------------------------
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
        "description": f"HiOrg {ov}",
        "userAccountControl": 512 if LDAP_CREATE_ENABLED else 514,
    }

    hid = _build_hiorg_id(person)
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


def _ldap_attr_set(changes: dict, attr: str, value: Any) -> None:
    if value is None:
        return
    v = str(value).strip()
    if v == "" and not LDAP_OVERWRITE_EMPTY:
        return
    if v == "":
        changes[attr] = [(MODIFY_REPLACE, [])]
    else:
        changes[attr] = [(MODIFY_REPLACE, [v])]


def _find_existing_by_hiorg_id(conn, search_base: str, hiorg_id: str) -> dict | None:
    if not hiorg_id:
        return None
    return ldap_search_one(
        conn,
        search_base,
        f"({LDAP_HIORG_ID_ATTR}={hiorg_id})",
        ["distinguishedName", "sAMAccountName", LDAP_HIORG_ID_ATTR],
    )


def _move_if_needed(conn, dn: str, target_ou: str) -> str:
    if not LDAP_MOVE_IF_OU_CHANGED:
        return dn
    if dn.lower().endswith("," + target_ou.lower()):
        return dn
    rdn = dn.split(",", 1)[0]
    ok = conn.modify_dn(dn, relative_dn=rdn, new_superior=target_ou)
    if not ok:
        return dn
    return f"{rdn},{target_ou}"


# -----------------------------
# Group sync (optional, via groupmap.json)
# -----------------------------
def _resolve_ad_group_dn(ov: str, hiorg_group_name: str) -> tuple[str | None, str]:
    m = load_groupmap(ov)
    gcfg = (m.get("groups") or {}).get(hiorg_group_name)
    if not gcfg:
        return None, "no_mapping"

    base_dn = str(gcfg.get("base_dn") or "").strip()
    if not base_dn:
        loc = str(gcfg.get("location") or "").strip()
        base_dn = str(((m.get("locations") or {}).get(loc, {}) or {}).get("base_dn", "")).strip()

    if not base_dn:
        return None, "no_base_dn"

    ad_cn = str(gcfg.get("ad_cn") or "").strip()
    cn = (ad_cn if ad_cn else hiorg_group_name).strip()

    if "," in cn and cn.upper().startswith("CN="):
        return cn, "ok"

    if cn.upper().startswith("CN="):
        cn = cn[3:].strip()

    return f"CN={escape_rdn(cn)},{base_dn}", "ok"


def _group_exists(conn, group_dn: str) -> bool:
    ok = conn.search(
        search_base=group_dn,
        search_filter="(objectClass=group)",
        search_scope=BASE,
        attributes=["distinguishedName"],
        size_limit=1,
    )
    return bool(ok and conn.entries)


def _sync_user_groups(conn, ov: str, user_dn: str, person: dict) -> dict:
    desired = {str(x).strip() for x in _hiorg_groups(person) if str(x).strip()}

    m = load_groupmap(ov)
    managed_groups = set((m.get("groups") or {}).keys())

    add_ok, add_skipped, remove_ok = [], [], []

    for g in sorted(desired):
        group_dn, reason = _resolve_ad_group_dn(ov, g)
        if not group_dn:
            add_skipped.append({"group": g, "reason": reason})
            continue
        if not _group_exists(conn, group_dn):
            add_skipped.append({"group": g, "reason": "ad_group_missing", "dn": group_dn})
            continue

        ok = conn.modify(group_dn, {LDAP_GROUP_MEMBER_ATTR: [(MODIFY_ADD, [user_dn])]} )
        code = (conn.result or {}).get("result")
        if ok or code in (0, 20):
            add_ok.append({"group": g, "dn": group_dn})
        else:
            add_skipped.append({"group": g, "reason": f"ldap_error_{code}", "dn": group_dn, "detail": conn.result})

    if LDAP_GROUP_SYNC_REMOVE:
        for g in sorted(managed_groups - desired):
            group_dn, reason = _resolve_ad_group_dn(ov, g)
            if not group_dn or not _group_exists(conn, group_dn):
                continue
            ok = conn.modify(group_dn, {LDAP_GROUP_MEMBER_ATTR: [(MODIFY_DELETE, [user_dn])]} )
            if ok:
                remove_ok.append({"group": g, "dn": group_dn})

    return {"desired_count": len(desired), "added": add_ok, "skipped": add_skipped, "removed": remove_ok}


# -----------------------------
# Routes
# -----------------------------
@router.get("/sync/run")
def sync_run(request: Request, ov: str = ""):
    require_api_key(request)

    ovs = [ov] if ov else get_ov_list()
    if not ovs:
        raise HTTPException(400, "No ov given and OV_LIST is empty")
    for one in ovs:
        require_ov(one)

    results = []
    for one in ovs:
        tokens = refresh_tokens(one)
        access = tokens.get("access_token")
        if not access:
            raise HTTPException(500, f"No access_token after refresh for ov '{one}'")

        marker = _get_marker(one)
        people = fetch_personal_updated_since(access, marker)

        new_marker = _iso(_now_utc() - timedelta(seconds=120))
        _set_marker(one, new_marker)

        results.append({"ov": one, "updated_since_used": marker, "fetched": len(people), "new_marker": new_marker})

    return {"ok": True, "results": results}


@router.get("/debug/personal")
def debug_personal(request: Request, ov: str, limit: int = 3):
    require_api_key(request)
    require_ov(ov)

    tokens = refresh_tokens(ov)
    access = tokens.get("access_token")
    if not access:
        raise HTTPException(500, f"No access_token after refresh for ov '{ov}'")

    marker = _get_marker(ov)
    people = fetch_personal_updated_since(access, marker)

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


@router.get("/debug/admap")
def debug_admap(request: Request, ov: str, limit: int = 3, dry_run: int = 1):
    require_api_key(request)
    require_ov(ov)

    tokens = refresh_tokens(ov)
    access = tokens.get("access_token")
    marker = _get_marker(ov)
    people = fetch_personal_updated_since(access, marker)

    ou_map = load_ou_map()
    target_ou = ou_map.get(ov.lower())
    if not target_ou:
        raise HTTPException(500, f"No OU mapping for ov '{ov}'. Set LDAP_OU_MAP in .env")

    people = people[: max(0, min(limit, 20))]

    conn = ldap_conn()
    out = []
    for p in people:
        attrs = p.get("attributes") or {}
        if (attrs.get("orgakuerzel") or "").lower() in {x.lower() for x in EXCLUDE_ORGAKUERZEL}:
            continue
        if LDAP_ONLY_STATUS_ACTIVE and (attrs.get("status") != "aktiv"):
            continue

        base_sam, h_username = _sam_base_from_person(attrs)
        sam = _ensure_unique_sam(conn, target_ou, base_sam, h_username)

        mapped = _map_person_to_ad_attrs(p, sam)
        dn = f"CN={escape_rdn(str(mapped.get('displayName','User')))}, {target_ou}".replace(" ,", ",")
        out.append({"person_id": p.get("id"), "dn": dn, "sam": sam, "attrs": mapped})

    conn.unbind()
    return {"ov": ov, "target_ou": target_ou, "count_preview": len(out), "preview": out, "dry_run": bool(dry_run)}


@router.get("/sync/ad")
def sync_ad(request: Request, ov: str, limit: int = 0, dry_run: int = 0):
    require_api_key(request)
    require_ov(ov)

    ou_map = load_ou_map()
    target_ou = ou_map.get(ov.lower())
    if not target_ou:
        raise HTTPException(500, f"No OU mapping for ov '{ov}'. Set LDAP_OU_MAP in .env")

    tokens = refresh_tokens(ov)
    access = tokens.get("access_token")
    if not access:
        raise HTTPException(500, f"No access_token after refresh for ov '{ov}'")

    marker = _get_marker(ov)
    people = fetch_personal_updated_since(access, marker)

    if limit and limit > 0:
        people = people[:limit]

    conn = ldap_conn()
    results = []
    incoming = 0

    excluded = {x.lower() for x in EXCLUDE_ORGAKUERZEL}

    for p in people:
        attrs = p.get("attributes") or {}
        org = (attrs.get("orgakuerzel") or "").lower()
        status = attrs.get("status") or ""

        if org in excluded:
            continue
        if LDAP_ONLY_STATUS_ACTIVE and status != "aktiv":
            continue

        incoming += 1

        hid = _build_hiorg_id(p)
        existing = _find_existing_by_hiorg_id(conn, target_ou, hid)

        base_sam, fallback_username = _sam_base_from_person(attrs)

        if existing and not LDAP_UPDATE_SAM:
            sam = (existing.get("attributes") or {}).get("sAMAccountName")
            if isinstance(sam, list) and sam:
                sam = sam[0]
            if not isinstance(sam, str) or not sam:
                sam = _ensure_unique_sam(conn, target_ou, base_sam, fallback_username)
        else:
            sam = _ensure_unique_sam(conn, target_ou, base_sam, fallback_username)

        mapped = _map_person_to_ad_attrs(p, sam)
        display = mapped.get("displayName") or mapped.get("cn") or "User"
        dn_target = f"CN={escape_rdn(str(display))},{target_ou}"

        if dry_run:
            results.append({
                "person_id": p.get("id"),
                "action": "dry_run",
                "dn": dn_target,
                "sam": sam,
                "mapped_keys": sorted(mapped.keys()),
            })
            continue

        if not existing:
            ok = conn.add(dn_target, attributes=mapped)
            group_sync = _sync_user_groups(conn, ov, dn_target, p) if ok else {}
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

        dn_existing = existing["dn"]
        dn_after = _move_if_needed(conn, dn_existing, target_ou)

        changes: dict[str, Any] = {}
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
        if hid:
            _ldap_attr_set(changes, LDAP_HIORG_ID_ATTR, hid)

        ok = True
        if changes:
            ok = conn.modify(dn_after, changes)

        group_sync = _sync_user_groups(conn, ov, dn_after, p) if ok else {}
        results.append({
            "person_id": p.get("id"),
            "action": "update",
            "ok": bool(ok),
            "dn": dn_after,
            "sam": sam,
            "changed_attrs": sorted(changes.keys()),
            "group_sync": group_sync,
            "result": conn.result,
        })

    conn.unbind()

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

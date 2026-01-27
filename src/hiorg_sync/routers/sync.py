from ldap3.utils.dn import escape_rdn

from datetime import timedelta

from fastapi import APIRouter, Request, HTTPException
from .. import legacy

router = APIRouter()

# Bridge: make legacy helpers/constants available in this module.
# NOTE: 'from legacy import *' would NOT import names starting with '_'!
globals().update({k: v for k, v in vars(legacy).items() if k.startswith('_') or k.isupper()})

@router.get("/sync/run")
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


@router.get("/debug/personal")
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


@router.get("/debug/admap")
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

@router.get("/sync/ad")
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

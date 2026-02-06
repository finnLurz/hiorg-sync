# src/hiorg_sync/routers/sync.py
from __future__ import annotations

import json
import re
import secrets
import unicodedata
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Request, HTTPException
from ldap3 import MODIFY_ADD, MODIFY_DELETE, MODIFY_REPLACE, BASE
from ldap3.utils.dn import escape_rdn


from ..services.notify import send_mail
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
from ..services.groupmap_store import load_groupmap, resolve_group_base_dn

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
# Notify (queue + rate limit) per OV
# -----------------------------
def _notify_last_sent_path(ov: str) -> Path:
    return _ov_dir(ov) / "notify_last_sent.txt"


def _notify_queue_path(ov: str) -> Path:
    return _ov_dir(ov) / "notify_queue.json"


def _read_last_sent(ov: str) -> datetime | None:
    p = _notify_last_sent_path(ov)
    if not p.exists():
        return None
    s = p.read_text(encoding="utf-8").strip()
    if not s:
        return None
    try:
        if s.endswith("Z"):
            s = s.replace("Z", "+00:00")
        return datetime.fromisoformat(s).astimezone(timezone.utc)
    except Exception:
        return None


def _write_last_sent(ov: str, dt: datetime) -> None:
    _notify_last_sent_path(ov).write_text(_iso(dt) + "\n", encoding="utf-8")


def _load_queue(ov: str) -> list[dict]:
    p = _notify_queue_path(ov)
    if not p.exists():
        return []
    try:
        return json.loads(p.read_text(encoding="utf-8") or "[]")
    except Exception:
        return []


def _save_queue(ov: str, items: list[dict]) -> None:
    _notify_queue_path(ov).write_text(json.dumps(items, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


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


def _ldap_val_to_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, list):
        return [str(x) for x in v if str(x).strip()]
    s = str(v).strip()
    return [s] if s else []


def _get_existing_attr(existing: dict | None, attr: str) -> list[str]:
    if not existing:
        return []
    a = (existing.get("attributes") or {})
    return _ldap_val_to_list(a.get(attr))


def _get_mapped_attr(mapped: dict, attr: str) -> list[str]:
    return _ldap_val_to_list(mapped.get(attr))


WATCH_PHONE = [
    "telephoneNumber", "homePhone", "mobile", "ipPhone",
    "otherTelephone", "otherHomePhone", "otherMobile",
    "pager", "facsimileTelephoneNumber",
]
WATCH_ADDR = ["streetAddress", "postalCode", "l", "co"]
WATCH_MAIL = ["mail", "proxyAddresses", "otherMailbox", "targetAddress"]
WATCH_ALL = WATCH_PHONE + WATCH_ADDR + WATCH_MAIL


def _diff_watched(existing: dict | None, mapped: dict[str, Any]) -> dict[str, dict]:
    """
    returns: {attr: {old: <...>, new: <...>}}
    - For list attrs: compare sets (case-insensitive)
    - For single attrs: compare first element string
    Only diffs fields that are present in mapped.
    """
    changes: dict[str, dict] = {}

    for attr in WATCH_ALL:
        if attr not in mapped:
            continue

        old_list = _get_existing_attr(existing, attr)
        new_list = _get_mapped_attr(mapped, attr)

        if attr in ("proxyAddresses", "otherTelephone", "otherHomePhone", "otherMobile"):
            old_set = {x.strip().lower() for x in old_list}
            new_set = {x.strip().lower() for x in new_list}
            if old_set != new_set:
                changes[attr] = {"old": old_list, "new": new_list}
            continue

        old = (old_list[0].strip() if old_list else "")
        new = (new_list[0].strip() if new_list else "")
        if old != new:
            changes[attr] = {"old": old, "new": new}

    return changes


def _find_existing_by_hiorg_id(conn, search_base: str, hiorg_id: str) -> dict | None:
    if not hiorg_id:
        return None
    return ldap_search_one(
        conn,
        search_base,
        f"({LDAP_HIORG_ID_ATTR}={hiorg_id})",
        [
            "distinguishedName", "sAMAccountName", LDAP_HIORG_ID_ATTR,

            # Phones
            "telephoneNumber", "homePhone", "mobile", "ipPhone",
            "otherTelephone", "otherHomePhone", "otherMobile",
            "pager", "facsimileTelephoneNumber",

            # Address
            "streetAddress", "postalCode", "l", "co",

            # Email
            "mail", "proxyAddresses", "otherMailbox", "targetAddress",

            "displayName",
        ],
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
# Group sync (optional, via groupmap.json + global baseDN)
# -----------------------------




def _resolve_ad_group_dn(ov: str, hiorg_group_name: str) -> tuple[str | None, str]:
    m = load_groupmap()
    gcfg = (m.get("groups") or {}).get(hiorg_group_name)
    if not isinstance(gcfg, dict):
        return None, "no_mapping"

    base_dn = resolve_group_base_dn(hiorg_group_name)
    if not base_dn:
        return None, "no_base_dn"

    ad_cn = str(gcfg.get("ad_cn") or "").strip()
    cn = (ad_cn if ad_cn else hiorg_group_name).strip()

    # Falls schon vollständiger DN übergeben wurde
    if "," in cn and cn.upper().startswith("CN="):
        return cn, "ok"

    # Falls "CN=xyz" übergeben wurde
    if cn.upper().startswith("CN="):
        cn = cn[3:].strip()

    if not cn:
        return None, "no_cn"

    return f"CN={escape_rdn(cn)},{base_dn}", "ok"

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
        raise HTTPException(
            500,
            f"No OU mapping for ov '{ov}'. Configure in UI (/ui/settings/ou-map) or set LDAP_OU_MAP_JSON env override.",
        )

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
def sync_ad(request: Request, ov: str, limit: int = 0, dry_run: int = 0, full: int = 0):
    require_api_key(request)
    require_ov(ov)

    ou_map = load_ou_map()
    target_ou = ou_map.get(ov.lower())
    if not target_ou:
        raise HTTPException(
            500,
            f"No OU mapping for ov '{ov}'. Configure in UI (/ui/settings/ou-map) or set LDAP_OU_MAP_JSON env override.",
        )

    tokens = refresh_tokens(ov)
    access = tokens.get("access_token")
    if not access:
        raise HTTPException(500, f"No access_token after refresh for ov '{ov}'")

    # Full Sync: Marker bewusst "ganz alt" setzen (ignoriert gespeicherten Marker)
    if full:
        marker = "1970-01-01T00:00:00Z"
    else:
        marker = _get_marker(ov)

    people = fetch_personal_updated_since(access, marker)
    if limit and limit > 0:
        people = people[:limit]

    # Notify config aus groupmap.json
    gm = load_groupmap()
    notify_cfg = gm.get("notify") or {}
    notify_enabled = bool(notify_cfg.get("enabled"))
    notify_to = str(notify_cfg.get("to") or "").strip()
    notify_subject_tpl = str(notify_cfg.get("subject") or "").strip()
    try:
        freq_hours = int(notify_cfg.get("freq_hours") or 0)
    except Exception:
        freq_hours = 0

    notifications: list[dict] = []

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

        watched_changes = _diff_watched(existing, mapped)

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

        if ok and watched_changes:
            old_display = ""
            try:
                old_display = (_get_existing_attr(existing, "displayName") or [""])[0]
            except Exception:
                old_display = ""

            notifications.append({
                "person_id": p.get("id"),
                "sam": sam,
                "display": str(mapped.get("displayName") or old_display or ""),
                "dn": dn_after,
                "changes": watched_changes,
            })

        group_sync = _sync_user_groups(conn, ov, dn_after, p) if ok else {}
        results.append({
            "person_id": p.get("id"),
            "action": "update",
            "ok": bool(ok),
            "dn": dn_after,
            "sam": sam,
            "changed_attrs": sorted(changes.keys()),
            "watched_changed_attrs": sorted(watched_changes.keys()),
            "group_sync": group_sync,
            "result": conn.result,
        })

    conn.unbind()

    new_marker = _iso(_now_utc() - timedelta(seconds=120))

    # Dry-run darf den Marker NICHT verändern
    marker_written = False
    if not dry_run:
        _set_marker(ov, new_marker)
        marker_written = True

    # -----------------------------
    # Notify: queue + rate-limit send (nur wenn nicht dry_run)
    # -----------------------------
    notify_sent = False
    notify_queued = 0
    notify_error = ""

    if (not dry_run) and notify_enabled and notify_to:
        q = _load_queue(ov)

        if notifications:
            ts = _iso(_now_utc())
            for n in notifications:
                n["ts"] = ts
                q.append(n)

            if len(q) > 2000:
                q = q[-2000:]

            _save_queue(ov, q)

        notify_queued = len(q)

        should_send = False
        if freq_hours <= 0:
            should_send = True
        else:
            last = _read_last_sent(ov)
            if (last is None) or ((_now_utc() - last).total_seconds() >= freq_hours * 3600):
                should_send = True

        if should_send and q:
            subject = notify_subject_tpl or "[HiOrg-Sync] Änderungen OV={ov} ({count})"
            subject = subject.replace("{ov}", ov).replace("{count}", str(len(q)))

            lines: list[str] = []
            lines.append(f"HiOrg-Sync Änderungsbericht OV={ov}")
            lines.append(f"Einträge: {len(q)}")
            lines.append("")

            for item in q[:500]:
                lines.append(
                    f"- {item.get('display','?')} "
                    f"(sam={item.get('sam','?')}, id={item.get('person_id','?')}, ts={item.get('ts','')})"
                )
                ch = item.get("changes") or {}
                for attr, diff in ch.items():
                    lines.append(f"    {attr}: {diff.get('old')} -> {diff.get('new')}")
                lines.append("")

            ok, err = send_mail(notify_to, subject, "\n".join(lines))
            if ok:
                _save_queue(ov, [])
                _write_last_sent(ov, _now_utc())
                notify_sent = True
                notify_queued = 0
            else:
                notify_error = err or "send failed"

    return {
        "ov": ov,
        "target_ou": target_ou,
        "updated_since_used": marker,
        "new_marker": new_marker,
        "full": bool(full),
        "dry_run": bool(dry_run),
        "marker_written": marker_written,
        "count_incoming": incoming,
        "results": results,
        "notify": {
            "enabled": bool(notify_enabled),
            "to": notify_to,
            "freq_hours": freq_hours,
            "queued_after_run": notify_queued,
            "sent": notify_sent,
            "error": notify_error,
        },
        "notes": {
            "overwrite_empty": LDAP_OVERWRITE_EMPTY,
            "only_status_active": LDAP_ONLY_STATUS_ACTIVE,
            "excluded_orgakuerzel": sorted(list(EXCLUDE_ORGAKUERZEL)),
            "hiorg_id_attr": LDAP_HIORG_ID_ATTR,
        },
    }

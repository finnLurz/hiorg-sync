from datetime import timedelta

import os

from fastapi import APIRouter, Request, HTTPException
from .. import legacy

router = APIRouter()
@router.get("/api/groups")
def api_groups(request: Request, ov: str, days: int = 3650):
    legacy._require_api_or_ui(request)
    legacy._require_ov(ov)

    tokens = legacy._refresh_tokens(ov)
    access = tokens.get("access_token")
    if not access:
        raise HTTPException(500, f"No access_token after refresh for ov '{ov}'")

    # Discovery unabhängig vom Sync-Marker (damit UI alles sieht)
    marker = legacy._iso(legacy._now_utc() - timedelta(days=days))
    people = legacy._fetch_personal_updated_since(access, marker)

    found: dict[str, dict] = {}

    for p in people:
        attrs = p.get("attributes") or {}
        if (attrs.get("orgakuerzel") or "").lower() in legacy.EXCLUDE_ORGAKUERZEL:
            continue
        if legacy.LDAP_ONLY_STATUS_ACTIVE and (attrs.get("status") != "aktiv"):
            continue

        loc = legacy._person_location(attrs)
        for g in legacy._hiorg_groups(p):
            g = str(g).strip()
            if not g:
                continue
            loc2, g2 = legacy._split_group_location(g)
            loc_final = loc or loc2 or "Unbekannt"
            found.setdefault(g2, {"locations": set()})["locations"].add(loc_final)

    out = [{"group": k, "locations": sorted(list(v["locations"]))} for k, v in sorted(found.items())]
    return {"ov": ov, "count": len(out), "groups": out}


@router.get("/api/groupmap")
def api_groupmap_get(request: Request, ov: str):
    legacy._require_api_or_ui(request)
    legacy._require_ov(ov)
    return {"ov": ov, "map": legacy._load_groupmap(ov)}

@router.post("/api/groupmap")
async def api_groupmap_post(request: Request, ov: str):
    legacy._require_api_or_ui(request)
    legacy._require_ov(ov)
    body = await request.json()
    m = body.get("map")
    if not isinstance(m, dict):
        raise HTTPException(400, "body.map must be a dict")
    legacy._save_groupmap(ov, m)
    return {"ok": True}
    


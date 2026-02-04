from __future__ import annotations

import os
import re
import urllib.request
import urllib.parse
import urllib.error
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks

from starlette.concurrency import run_in_threadpool

from ..core.security import require_api_or_ui
from ..core.settings import require_ov

from ..services.hiorg import refresh_tokens, fetch_personal_updated_since
from ..services.groupmap_store import load_groupmap, save_groupmap

router = APIRouter()

# --- Config / Filter ---
HIORG_LOCATION_KEY = os.getenv("HIORG_LOCATION_KEY", "standort")
HIORG_GROUP_SPLIT_RE = os.getenv("HIORG_GROUP_SPLIT_RE", r"\s*::\s*")

SYNC_AD_URL = os.getenv("SYNC_AD_URL", "http://127.0.0.1:8088/sync/ad")

EXCLUDE_ORGAKUERZEL = {
    x.strip().lower()
    for x in os.getenv("EXCLUDE_ORGAKUERZEL", "stab04").split(",")
    if x.strip()
}

LDAP_ONLY_STATUS_ACTIVE = os.getenv("LDAP_ONLY_STATUS_ACTIVE", "true").lower() in ("1", "true", "yes")


# --- small helpers (previously in legacy) ---
def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def person_location(attrs: dict) -> str:
    return str(attrs.get(HIORG_LOCATION_KEY, "") or "").strip()


def split_group_location(group_name: str) -> tuple[str, str]:
    parts = re.split(HIORG_GROUP_SPLIT_RE, str(group_name), maxsplit=1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "", str(group_name).strip()


def hiorg_groups(person: dict) -> list[str]:
    attrs = person.get("attributes") or {}
    g = attrs.get("gruppen_namen")
    if isinstance(g, list):
        return [str(x) for x in g]
    return []


def _do_sync_call(url: str, api_key: str) -> tuple[int, str]:
    req = urllib.request.Request(
        url,
        method="GET",
        headers={"X-API-Key": api_key},
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return resp.status, body
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        return e.code, body

@router.get("/api/groups")
def api_groups(request: Request, ov: str, days: int = 3650):
    require_api_or_ui(request)
    require_ov(ov)

    tokens = refresh_tokens(ov)
    access = tokens.get("access_token")
    if not access:
        raise HTTPException(500, f"No access_token after refresh for ov '{ov}'")

    # Discovery unabhängig vom Sync-Marker (damit UI alles sieht)
    marker = iso(now_utc() - timedelta(days=days))
    people = fetch_personal_updated_since(access, marker)

    found: dict[str, dict] = {}

    for p in people:
        attrs = p.get("attributes") or {}

        if (attrs.get("orgakuerzel") or "").lower() in EXCLUDE_ORGAKUERZEL:
            continue
        if LDAP_ONLY_STATUS_ACTIVE and (attrs.get("status") != "aktiv"):
            continue

        loc = person_location(attrs)

        for g in hiorg_groups(p):
            g = str(g).strip()
            if not g:
                continue

            loc2, g2 = split_group_location(g)
            loc_final = loc or loc2 or "Unbekannt"
            found.setdefault(g2, {"locations": set()})["locations"].add(loc_final)

    out = [{"group": k, "locations": sorted(list(v["locations"]))} for k, v in sorted(found.items())]
    return {"ov": ov, "count": len(out), "groups": out}


@router.get("/api/groupmap")
def api_groupmap_get(request: Request, ov: str):
    require_api_or_ui(request)
    require_ov(ov)
    return {"ov": ov, "map": load_groupmap(ov)}


@router.post("/api/groupmap")
async def api_groupmap_post(request: Request, ov: str):
    require_api_or_ui(request)
    require_ov(ov)

    body = await request.json()
    m = body.get("map")
    if not isinstance(m, dict):
        raise HTTPException(400, "body.map must be a dict")

    save_groupmap(ov, m)
    return {"ok": True}
    
    
@router.post("/api/sync/ad/run")
async def run_sync_ad(
    request: Request,
    background_tasks: BackgroundTasks,
    ov: str,
    full: int = 0,
    dry_run: int = 0,
):
    require_api_or_ui(request)
    require_ov(ov)

    api_key = os.getenv("SYNC_API_KEY", "")
    if not api_key or api_key == "BITTEAENDERN":
        raise HTTPException(status_code=500, detail="SYNC_API_KEY not configured")

    params = {"ov": ov}
    if full:
        params["full"] = "1"
    if dry_run:
        params["dry_run"] = "1"

    base = SYNC_AD_URL.rstrip("?")
    url = base + "?" + urllib.parse.urlencode(params)

    async def _runner():
        status, body = await run_in_threadpool(_do_sync_call, url, api_key)
        # optional: loggen, damit du Ergebnis siehst
        print(f"[sync/ad] ov={ov} full={full} dry={dry_run} -> {status}")
        if status != 200:
            print(body[:2000])

    background_tasks.add_task(_runner)

    return {"ok": True, "started": True, "ov": ov, "full": bool(full), "dry_run": bool(dry_run)}
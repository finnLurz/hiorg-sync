# src/hiorg_sync/routers/oauth.py
from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from urllib.parse import urlencode

from ..core.settings import (
    HIORG_CLIENT_ID,
    HIORG_CLIENT_SECRET,
    HIORG_REDIRECT_URI,
    HIORG_AUTH_URL,
    require_ov,
    HIORG_SCOPE,
)
from ..core.storage import load_states, save_states, save_tokens
from ..services.hiorg import gen_state, token_request

router = APIRouter()


@router.get("/oauth/start")
def oauth_start(ov: str):
    require_ov(ov)

    if not (HIORG_CLIENT_ID and HIORG_CLIENT_SECRET and HIORG_REDIRECT_URI):
        raise HTTPException(500, "Missing HIORG_CLIENT_ID/SECRET/REDIRECT_URI env vars")

    state = gen_state(ov)

    states = load_states()
    states[state] = {"ov": ov}
    save_states(states)

    q = {
        "response_type": "code",
        "client_id": HIORG_CLIENT_ID,
        "redirect_uri": HIORG_REDIRECT_URI,
        "scope": HIORG_SCOPE,   # wichtig: scope muss hier rein
        "state": state,
    }
    return RedirectResponse(f"{HIORG_AUTH_URL}?{urlencode(q)}", status_code=302)


@router.get("/oauth/callback")
def oauth_callback(code: str = "", state: str = "", error: str = "", error_description: str = ""):
    if error:
        raise HTTPException(400, f"OAuth error: {error} {error_description}".strip())
    if not code or not state:
        raise HTTPException(400, "Missing code/state")

    states = load_states()
    entry = states.get(state)
    if not entry:
        raise HTTPException(400, "Invalid/expired state")

    ov = str(entry.get("ov", "") or "")
    require_ov(ov)

    # state einmalig verbrauchen
    states.pop(state, None)
    save_states(states)

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": HIORG_REDIRECT_URI,
        "client_id": HIORG_CLIENT_ID,
        "client_secret": HIORG_CLIENT_SECRET,
    }
    tokens = token_request(payload)
    save_tokens(ov, tokens)

    return JSONResponse({"ok": True, "ov": ov, "stored": True, "next": f"/sync/run?ov={ov}"})

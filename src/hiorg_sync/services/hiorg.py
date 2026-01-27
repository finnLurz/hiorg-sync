# src/hiorg_sync/services/hiorg.py
from __future__ import annotations

import hashlib
import time
from typing import Any, Dict, List, Optional
from urllib.parse import urlencode

import requests
from fastapi import HTTPException

from ..core.settings import (
    HIORG_API_BASE,
    HIORG_AUTH_URL,
    HIORG_TOKEN_URL,
    HIORG_CLIENT_ID,
    HIORG_CLIENT_SECRET,
    HIORG_REDIRECT_URI,
    HIORG_SCOPE,
    STATE_SECRET,
)
from ..core.storage import load_tokens, save_tokens


ACCEPT_HEADER = "application/vnd.api+json"
DEFAULT_TIMEOUT_TOKEN = 30
DEFAULT_TIMEOUT_API = 60


# -----------------------------
# OAuth helpers
# -----------------------------
def _secrets_token(nbytes: int = 16) -> str:
    import secrets

    # token_urlsafe(16) liefert typischerweise ~22 chars
    return secrets.token_urlsafe(nbytes)


def gen_state(ov: str) -> str:
    """
    Deterministic-ish state (hash) based on secret + ov + timestamp + random.
    Good enough for CSRF state; you still need to STORE it server-side for validation.
    """
    ts = str(int(time.time()))
    rnd = _secrets_token(16)
    raw = f"{STATE_SECRET}|{ov}|{ts}|{rnd}".encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def build_auth_url(state: str) -> str:
    """
    Builds the HiOrg authorize URL (OAuth).
    """
    if not (HIORG_CLIENT_ID and HIORG_REDIRECT_URI):
        raise HTTPException(500, "Missing HIORG_CLIENT_ID/HIORG_REDIRECT_URI")

    q = {
        "response_type": "code",
        "client_id": HIORG_CLIENT_ID,
        "redirect_uri": HIORG_REDIRECT_URI,
        "scope": HIORG_SCOPE,
        "state": state,
    }
    return f"{HIORG_AUTH_URL}?{urlencode(q)}"


# -----------------------------
# Token endpoints
# -----------------------------
def token_request(payload: Dict[str, str]) -> Dict[str, Any]:
    """
    Generic token endpoint call. Raises HTTPException with helpful error text.
    """
    try:
        r = requests.post(HIORG_TOKEN_URL, data=payload, timeout=DEFAULT_TIMEOUT_TOKEN)
    except requests.RequestException as e:
        raise HTTPException(502, f"Token request failed (network): {e!s}")

    if r.status_code >= 400:
        # HiOrg liefert oft JSON mit error/error_description; gib das lesbar aus
        try:
            j = r.json()
            msg = j.get("error_description") or j.get("message") or r.text
        except Exception:
            msg = r.text
        raise HTTPException(r.status_code, f"Token request failed: {msg}")

    try:
        return r.json()
    except Exception:
        raise HTTPException(502, f"Token response not JSON: {r.text[:500]}")


def exchange_code_for_tokens(code: str) -> Dict[str, Any]:
    """
    OAuth callback: exchange authorization code for tokens.
    """
    if not (HIORG_CLIENT_ID and HIORG_CLIENT_SECRET and HIORG_REDIRECT_URI):
        raise HTTPException(500, "Missing HIORG_CLIENT_ID/SECRET/REDIRECT_URI")

    payload = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": HIORG_REDIRECT_URI,
        "client_id": HIORG_CLIENT_ID,
        "client_secret": HIORG_CLIENT_SECRET,
    }
    return token_request(payload)


def refresh_tokens(ov: str) -> Dict[str, Any]:
    """
    Refresh tokens for ov and persist them.
    Keeps old refresh_token if API doesn't return a new one.
    """
    tokens = load_tokens(ov)
    refresh = (tokens.get("refresh_token") or "").strip()
    if not refresh:
        raise HTTPException(412, f"No refresh_token stored for ov '{ov}'")

    payload = {
        "grant_type": "refresh_token",
        "refresh_token": refresh,
        "client_id": HIORG_CLIENT_ID,
        "client_secret": HIORG_CLIENT_SECRET,
    }
    new_tokens = token_request(payload)

    # falls kein neues refresh_token kommt: altes behalten
    if "refresh_token" not in new_tokens or not new_tokens.get("refresh_token"):
        new_tokens["refresh_token"] = refresh

    save_tokens(ov, new_tokens)
    return new_tokens


# -----------------------------
# API helpers
# -----------------------------
def api_get(
    access_token: str,
    path: str,
    params: Optional[Dict[str, str]] = None,
    url_override: Optional[str] = None,
) -> Dict[str, Any]:
    """
    GET wrapper for HiOrg API.
    """
    url = url_override or f"{HIORG_API_BASE}{path}"
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Accept": ACCEPT_HEADER,
    }

    try:
        r = requests.get(url, headers=headers, params=params, timeout=DEFAULT_TIMEOUT_API)
    except requests.RequestException as e:
        raise HTTPException(502, f"API GET failed (network): {e!s}")

    if r.status_code >= 400:
        try:
            j = r.json()
            # JSON:API error format
            if isinstance(j, dict) and "errors" in j and isinstance(j["errors"], list) and j["errors"]:
                detail = j["errors"][0].get("detail") or j["errors"][0].get("title") or r.text
            else:
                detail = r.text
        except Exception:
            detail = r.text
        raise HTTPException(r.status_code, f"API GET failed: {detail}")

    try:
        return r.json()
    except Exception:
        raise HTTPException(502, f"API response not JSON: {r.text[:500]}")


def fetch_personal_updated_since(access_token: str, updated_since: str) -> List[Dict[str, Any]]:
    """
    Fetch /personal with pagination using links.next.
    `updated_since` must be ISO string like 2020-01-01T00:00:00Z
    """
    params = {"filter[updated_since]": updated_since}
    data_all: List[Dict[str, Any]] = []

    first = api_get(access_token, "/personal", params=params)
    if isinstance(first, dict) and isinstance(first.get("data"), list):
        data_all.extend(first["data"])

    links = first.get("links") or {}
    next_url = links.get("next")

    def _abs(u: str) -> str:
        if u.startswith("http://") or u.startswith("https://"):
            return u
        # manche APIs liefern relative URLs
        return f"{HIORG_API_BASE}{u}"

    while next_url:
        page = api_get(access_token, "/personal", url_override=_abs(str(next_url)))
        if isinstance(page, dict) and isinstance(page.get("data"), list):
            data_all.extend(page["data"])
        links = page.get("links") or {}
        next_url = links.get("next")

    return data_all

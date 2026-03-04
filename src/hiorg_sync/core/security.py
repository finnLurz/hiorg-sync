import os
import json
import time
import hmac
import base64
import hashlib
import secrets

from fastapi import Request, HTTPException


UI_PASSWORD = os.getenv("UI_PASSWORD", "")  # wenn leer: UI ungeschützt
UI_SESSION_SECRET = os.getenv("UI_SESSION_SECRET", os.getenv("STATE_SECRET", "change-me"))
UI_SESSION_TTL_HOURS = int(os.getenv("UI_SESSION_TTL_HOURS", "12"))

# API-Key (Sync-Endpunkte absichern)
# Backward-Compat: akzeptiert SYNC_API_KEY oder API_KEY
SYNC_API_KEY = (os.getenv("SYNC_API_KEY", "") or os.getenv("API_KEY", "")).strip()



def _b64url_encode(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode("utf-8").rstrip("=")


def _b64url_decode(s: str) -> bytes:
    pad = "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(s + pad)


def ui_make_session() -> str:
    payload = {"ts": int(time.time()), "rnd": secrets.token_urlsafe(8)}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    body = _b64url_encode(raw).encode("utf-8")
    sig = hmac.new(UI_SESSION_SECRET.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"{body.decode('utf-8')}.{sig}"


def ui_verify_session(token: str) -> bool:
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


def require_ui_login(request: Request) -> None:
    if not UI_PASSWORD:
        return
    token = request.cookies.get("ui_session", "")
    if not ui_verify_session(token):
        raise HTTPException(401, "UI login required")


def require_api_key(request: Request) -> None:
    if not SYNC_API_KEY:
        return
    got = request.headers.get("X-API-Key", "")
    if got != SYNC_API_KEY:
        raise HTTPException(401, "Missing/invalid X-API-Key")


def require_api_or_ui(request: Request) -> None:
    # 1) API-Key erlaubt (curl/scripts)
    if SYNC_API_KEY and request.headers.get("X-API-Key", "") == SYNC_API_KEY:
        return
    # 2) sonst UI-Session
    require_ui_login(request)

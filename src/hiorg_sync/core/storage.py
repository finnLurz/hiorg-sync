from __future__ import annotations

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path

from fastapi import HTTPException

from .settings import get_ov_list, require_ov

# Basisverzeichnis für Persistenz
DATA_DIR = Path(__import__("os").environ.get("DATA_DIR", "/var/lib/hiorg-sync"))
INITIAL_SYNC_DAYS = int(__import__("os").environ.get("INITIAL_SYNC_DAYS", "365"))

DATA_DIR.mkdir(parents=True, exist_ok=True)


def ov_dir(ov: str) -> Path:
    require_ov(ov)
    d = DATA_DIR / ov
    d.mkdir(parents=True, exist_ok=True)
    return d


# ---------- groupmap ----------
def groupmap_path(ov: str) -> Path:
    return ov_dir(ov) / "groupmap.json"


def load_groupmap(ov: str) -> dict:
    p = groupmap_path(ov)
    if not p.exists():
        return {"version": 1, "locations": {}, "groups": {}}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {"version": 1, "locations": {}, "groups": {}}


def save_groupmap(ov: str, m: dict) -> None:
    groupmap_path(ov).write_text(json.dumps(m, indent=2, ensure_ascii=False), encoding="utf-8")


# ---------- tokens ----------
def tokens_path(ov: str) -> Path:
    return ov_dir(ov) / "tokens.json"


def load_tokens(ov: str) -> dict:
    p = tokens_path(ov)
    if not p.exists():
        raise HTTPException(412, f"No tokens stored for ov '{ov}'. Run /oauth/start?ov=... first.")
    return json.loads(p.read_text(encoding="utf-8"))


def save_tokens(ov: str, tokens: dict) -> None:
    tokens_path(ov).write_text(json.dumps(tokens, indent=2), encoding="utf-8")


# ---------- marker ----------
def marker_path(ov: str) -> Path:
    return ov_dir(ov) / "updated_since.txt"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def get_marker(ov: str) -> str:
    p = marker_path(ov)
    if p.exists():
        v = p.read_text(encoding="utf-8").strip()
        if v:
            return v
    return _iso(_now_utc() - timedelta(days=INITIAL_SYNC_DAYS))


def set_marker(ov: str, marker: str) -> None:
    marker_path(ov).write_text(marker.strip() + "\n", encoding="utf-8")


# ---------- oauth state store ----------
def state_path() -> Path:
    return DATA_DIR / "states.json"


def load_states() -> dict:
    p = state_path()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_states(states: dict) -> None:
    state_path().write_text(json.dumps(states, indent=2), encoding="utf-8")

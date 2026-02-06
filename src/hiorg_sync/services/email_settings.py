from __future__ import annotations

import os
from typing import Any

from .config_store import read_json, EMAIL_PATH


def _env_bool(name: str, default: bool = False) -> bool:
    v = str(os.getenv(name, "")).strip().lower()
    if not v:
        return default
    return v in ("1", "true", "yes", "on")


def load_email_settings() -> dict[str, Any]:
    """
    Priority:
    1) email.json in DATA_DIR/settings (persisted)
    2) env overrides (any env var set wins)
    """
    cfg = read_json(EMAIL_PATH, default={})
    if not isinstance(cfg, dict):
        cfg = {}

    # normalize defaults
    out: dict[str, Any] = {
        "SMTP_HOST": str(cfg.get("SMTP_HOST", "") or "").strip(),
        "SMTP_PORT": int(cfg.get("SMTP_PORT", 587) or 587),
        "SMTP_USER": str(cfg.get("SMTP_USER", "") or "").strip(),
        "SMTP_PASS": str(cfg.get("SMTP_PASS", "") or ""),  # keep raw
        "SMTP_STARTTLS": bool(cfg.get("SMTP_STARTTLS", True)),
        "SMTP_SSL": bool(cfg.get("SMTP_SSL", False)),
        "NOTIFY_FROM": str(cfg.get("NOTIFY_FROM", "") or "").strip(),
        "SMTP_FROM": str(cfg.get("SMTP_FROM", "") or "").strip(),
    }

    # env overrides (hard override)
    if os.getenv("SMTP_HOST") is not None:
        out["SMTP_HOST"] = os.getenv("SMTP_HOST", "").strip()
    if os.getenv("SMTP_PORT") is not None:
        try:
            out["SMTP_PORT"] = int(os.getenv("SMTP_PORT", "587"))
        except Exception:
            pass
    if os.getenv("SMTP_USER") is not None:
        out["SMTP_USER"] = os.getenv("SMTP_USER", "").strip()
    if os.getenv("SMTP_PASS") is not None:
        out["SMTP_PASS"] = os.getenv("SMTP_PASS", "")
    if os.getenv("SMTP_STARTTLS") is not None:
        out["SMTP_STARTTLS"] = _env_bool("SMTP_STARTTLS", default=out["SMTP_STARTTLS"])
    if os.getenv("SMTP_SSL") is not None:
        out["SMTP_SSL"] = _env_bool("SMTP_SSL", default=out["SMTP_SSL"])
    if os.getenv("NOTIFY_FROM") is not None:
        out["NOTIFY_FROM"] = os.getenv("NOTIFY_FROM", "").strip()
    if os.getenv("SMTP_FROM") is not None:
        out["SMTP_FROM"] = os.getenv("SMTP_FROM", "").strip()

    return out

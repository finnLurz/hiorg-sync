from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


def _data_dir() -> str:
    # NICHT aus core.settings importieren -> sonst Circular Import mÃÂ¶glich
    return os.getenv("DATA_DIR", "/var/lib/hiorg-sync")


SETTINGS_DIR = Path(_data_dir()) / "settings"
OU_MAP_PATH = SETTINGS_DIR / "ou_map.json"
EMAIL_PATH = SETTINGS_DIR / "email.json"
CONFIG_PATH = SETTINGS_DIR / "config.json"
LDAP_PATH = SETTINGS_DIR / "ldap.json"


def ensure_settings_dir() -> None:
    SETTINGS_DIR.mkdir(parents=True, exist_ok=True)


def read_json(path: Path, default: Any) -> Any:
    try:
        if not path.exists():
            return default
        raw = path.read_text(encoding="utf-8").strip()
        if not raw:
            return default
        return json.loads(raw)
    except Exception:
        return default


def write_json_atomic(path: Path, data: Any) -> None:
    ensure_settings_dir()
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, path)


def read_config(default: dict | None = None) -> dict:
    cfg = read_json(CONFIG_PATH, default=default or {})
    return cfg if isinstance(cfg, dict) else {}


def write_config_patch(patch: dict) -> dict:
    """
    Merge-Patch: aktualisiert nur Keys aus patch, lÃÂ¶scht nichts.
    -> verhindert, dass andere Bereiche (z.B. ov_list) ÃÂ¼berschrieben werden.
    """
    cfg = read_config(default={})
    for k, v in (patch or {}).items():
        cfg[k] = v
    write_json_atomic(CONFIG_PATH, cfg)
    return cfg

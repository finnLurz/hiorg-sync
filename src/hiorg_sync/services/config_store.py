from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from ..core.settings import DATA_DIR

SETTINGS_DIR = Path(DATA_DIR) / "settings"
OU_MAP_PATH = SETTINGS_DIR / "ou_map.json"
EMAIL_PATH = SETTINGS_DIR / "email.json"
CONFIG_PATH = SETTINGS_DIR / "config.json"


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

import os
import json
from pathlib import Path

DATA_DIR = Path(os.getenv("DATA_DIR", "/var/lib/hiorg-sync"))


def ov_dir(ov: str) -> Path:
    d = DATA_DIR / ov
    d.mkdir(parents=True, exist_ok=True)
    return d


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

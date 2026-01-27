"""
legacy.py: Compatibility layer.

Keep old imports working while code is being migrated to core/* modules.
"""

from __future__ import annotations

# settings
from .core.settings import get_ov_list, require_ov

# security
from .core.security import (
    UI_PASSWORD,
    UI_SESSION_TTL_HOURS,
    ui_make_session as _ui_make_session,
    require_ui_login as _require_ui_login,
    require_api_or_ui as _require_api_or_ui,
)

# storage (old names -> new functions)
from .core.storage import (
    load_groupmap as _load_groupmap,
    save_groupmap as _save_groupmap,
    load_tokens as _load_tokens,
    save_tokens as _save_tokens,
    load_states as _load_states,
    save_states as _save_states,
    get_marker as _get_marker,
    set_marker as _set_marker,
    marker_path as _marker_path,
)

def _require_ov(ov: str) -> None:
    return require_ov(ov)

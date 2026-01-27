import os


def get_ov_list() -> list[str]:
    """
    OV list comes from env var OV_LIST.
    IMPORTANT: no defaults here (so we don't leak internal IDs in source code).
    """
    raw = os.environ.get("OV_LIST", "")
    return [x.strip() for x in raw.split(",") if x.strip()]

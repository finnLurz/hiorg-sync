# Compatibility wrapper (so existing Docker/uvicorn commands keep working)
from hiorg_sync.main import app  # noqa: F401

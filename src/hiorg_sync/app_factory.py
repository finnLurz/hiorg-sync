from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .routers.ui import router as ui_router
from .routers.api import router as api_router
from .routers.oauth import router as oauth_router
from .routers.sync import router as sync_router
from .routers.misc import router as misc_router


def create_app() -> FastAPI:
    app = FastAPI(title="hiorg-sync")

    # Routers
    app.include_router(misc_router)
    app.include_router(ui_router)
    app.include_router(api_router)
    app.include_router(oauth_router)
    app.include_router(sync_router)

    base_dir = Path(__file__).resolve().parent

    # Static files (CSS/JS)
    static_dir = base_dir / "web" / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

    # Templates (Jinja2)
    templates_dir = base_dir / "web" / "templates"
    app.state.templates = Jinja2Templates(directory=str(templates_dir))

    return app

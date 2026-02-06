# src/hiorg_sync/routers/misc.py
from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter()


@router.get("/")
def root():
    return RedirectResponse("/ui/", status_code=302)


@router.get("/ui")
def ui_no_slash():
    return RedirectResponse("/ui/", status_code=302)

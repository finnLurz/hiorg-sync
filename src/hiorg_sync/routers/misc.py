from fastapi import APIRouter
from fastapi.responses import RedirectResponse

router = APIRouter()

@router.get("/ui", include_in_schema=False)
@router.get("/ui/", include_in_schema=False)
def ui_root():
    return RedirectResponse("/ui/ov", status_code=302)

@router.get("/", include_in_schema=False)
def root():
    return RedirectResponse("/ui/ov", status_code=302)

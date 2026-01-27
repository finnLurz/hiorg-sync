import secrets

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import RedirectResponse

from ..core.settings import get_ov_list, require_ov
from ..core.security import (
    UI_PASSWORD,
    UI_SESSION_TTL_HOURS,
    ui_make_session,
    require_ui_login,
)

router = APIRouter()


def _templates(request: Request):
    # app_factory.py setzt: app.state.templates = Jinja2Templates(...)
    return request.app.state.templates


@router.get("/ui/login")
def ui_login_get(request: Request, next: str = "/ui/ov"):
    return _templates(request).TemplateResponse(
        "login.html",
        {
            "request": request,
            "next": next,
            "ttl_hours": UI_SESSION_TTL_HOURS,
        },
    )


@router.post("/ui/login")
async def ui_login_post(request: Request):
    form = await request.form()
    pw = str(form.get("password", "") or "")
    nxt = str(form.get("next", "") or "/ui/ov")

    if UI_PASSWORD and not secrets.compare_digest(pw, UI_PASSWORD):
        raise HTTPException(401, "Invalid password")

    token = ui_make_session()
    resp = RedirectResponse(url=nxt, status_code=302)
    resp.set_cookie(
        "ui_session",
        token,
        httponly=True,
        secure=False,  # hinter HTTPS Proxy -> True
        samesite="lax",
        max_age=UI_SESSION_TTL_HOURS * 3600,
    )
    return resp


@router.get("/ui/ov")
def ui_ov_get(request: Request, next: str = "/ui/groupmap"):
    try:
        require_ui_login(request)
    except HTTPException:
        return RedirectResponse("/ui/login?next=/ui/ov", status_code=302)

    current = request.cookies.get("ui_ov", "")
    ovs = get_ov_list()

    return _templates(request).TemplateResponse(
        "ov.html",
        {
            "request": request,
            "ovs": ovs,
            "current": current,
            "next": next,
        },
    )


@router.post("/ui/ov")
async def ui_ov_post(request: Request):
    try:
        require_ui_login(request)
    except HTTPException:
        return RedirectResponse("/ui/login?next=/ui/ov", status_code=302)

    form = await request.form()
    ov = str(form.get("ov", "") or "").strip()

    if ov not in get_ov_list():
        raise HTTPException(400, "Invalid ov")

    url = f"/ui/groupmap?ov={ov}"
    resp = RedirectResponse(url=url, status_code=302)
    resp.set_cookie(
        "ui_ov",
        ov,
        httponly=True,
        secure=False,
        samesite="lax",
        max_age=UI_SESSION_TTL_HOURS * 3600,
    )
    return resp


@router.get("/ui/groupmap")
def ui_groupmap(request: Request, ov: str | None = None):
    try:
        require_ui_login(request)
    except HTTPException:
        return RedirectResponse("/ui/login?next=/ui/ov", status_code=302)

    if not ov:
        ov = request.cookies.get("ui_ov")
    if not ov:
        return RedirectResponse("/ui/ov", status_code=302)

    require_ov(ov)

    return _templates(request).TemplateResponse(
        "groupmap.html",
        {
            "request": request,
            "ov": ov,
        },
    )
